"""
EmbeddingService: lifecycle and plumbing of the embedding llama-server
instance used by the RAG pipeline (phase 2, milestone 4).

One instance per chat session. It owns:

- resolution of the configured embedding model (config "embedding_model",
  either a preset name or a GGUF filename) and of its retrieval metadata
  (pooling, asymmetric query/document prefixes) from the preset table;
- the LAZY start of the second llama-server: nothing runs until the first
  moment embeddings are actually needed — the first over-budget /attach,
  or the opening of a session that resumes a conversation with indexed
  documents. Most chat sessions never pay the ~650 MB / few seconds this
  server costs. Once started it stays up for the whole session (every
  search_documents call needs it to embed the query) and the CLI stops it
  in its shutdown path.
- indexing: text -> chunks -> embeddings -> documents/chunks tables.

Why the start cannot happen inside the search tool itself: the tool
registry executes tools as plain sync functions inside the agent's
running event loop, while LlamaServer.start() is async — so the CLI's
async code guarantees the server is up *before* the tool can ever run.

All user-facing failures raise EmbeddingUnavailable with an actionable
message (e.g. the exact download command); callers print it and keep the
session alive.
"""

from pathlib import Path
from typing import Callable, List, Optional

from llampaca.config import (
    MODELS_DIR,
    MODEL_PRESETS,
    get_preset_for_file,
    load_config,
)
from llampaca.engine.server import LlamaServer
from llampaca.engine.client import LlamaClient
from llampaca.engine import db
from llampaca import rag


class EmbeddingUnavailable(Exception):
    """
    Embeddings cannot be produced right now (model not downloaded, server
    failed to start, ...). The message is user-facing and actionable; the
    CLI shows it as a red [attach error] line and the session continues.
    """


class EmbeddingService:
    def __init__(self, db_path: Optional[Path] = None):
        """
        Args:
            db_path: Database override for tests; None uses the app db.

        Construction is cheap and never starts a server: it only resolves
        the configured model so that misconfiguration surfaces as a clear
        EmbeddingUnavailable at first use, not as a cryptic crash later.
        """
        self.db_path = db_path
        self._server: Optional[LlamaServer] = None
        self._client: Optional[LlamaClient] = None

        config = load_config()
        name = config.get("embedding_model", "")
        if name in MODEL_PRESETS:
            preset = MODEL_PRESETS[name]
            self.model_file = preset["file"]
        else:
            # A raw GGUF filename: recover the preset metadata by reverse
            # lookup when the file belongs to a known preset (the normal
            # case after `models download`), tolerate a fully custom file.
            self.model_file = name
            preset = get_preset_for_file(name) or {}

        # Model-specific retrieval metadata, with safe fallbacks for custom
        # models: "last" pooling matches current embedding architectures,
        # empty prefixes mean symmetric embedding.
        self.pooling = preset.get("pooling", "last")
        self.query_prefix = preset.get("query_prefix", "")
        self.document_prefix = preset.get("document_prefix", "")

    @property
    def started(self) -> bool:
        """Whether the embedding server is currently running."""
        return self._server is not None

    async def ensure_started(self) -> None:
        """
        Start the embedding llama-server if it is not running yet (the
        lazy-start entry point). Idempotent: a second call is a no-op.

        Raises:
            EmbeddingUnavailable: no model configured/downloaded, or the
                server did not come up healthy.
        """
        if self._server is not None:
            return

        if not self.model_file:
            raise EmbeddingUnavailable(
                "No embedding model configured. Download one with: "
                "llampaca models download --preset qwen3-embedding-0.6b"
            )
        model_path = MODELS_DIR / self.model_file
        if not model_path.exists():
            raise EmbeddingUnavailable(
                f"Embedding model '{self.model_file}' is not downloaded. "
                "Get it with: llampaca models download --preset qwen3-embedding-0.6b"
            )

        server = LlamaServer(
            model_path=model_path,
            embedding=True,
            pooling=self.pooling,
        )
        if not await server.start():
            raise EmbeddingUnavailable(
                "The embedding server failed to start; see its log in "
                "~/.llampaca/logs/ (llama-server-embed-*.log)."
            )
        self._server = server
        self._client = LlamaClient(port=server.port)

    async def index_document(
        self,
        conversation_id: str,
        filename: str,
        text: str,
    ) -> dict:
        """
        Index an extracted document for a conversation: chunk, embed (with
        the model's document prefix), and store. Starts the embedding
        server first if needed.

        Returns:
            {"document_id": int, "chunks": int, "pages": int | None,
             "dim": int} — for the CLI's progress/feedback lines.

        Raises:
            EmbeddingUnavailable: see ensure_started.
            ValueError: the text produced no chunks (cannot happen for
                text that passed extraction, which rejects empty results —
                kept as a guard for programmatic callers).
        """
        await self.ensure_started()

        chunks = rag.chunk_text(text)
        if not chunks:
            raise ValueError(f"'{filename}' produced no indexable chunks.")

        vectors = await self._client.embed(
            [self.document_prefix + chunk["text"] for chunk in chunks]
        )
        for chunk, vector in zip(chunks, vectors):
            chunk["embedding"] = rag.serialize_vector(vector)

        # Page count of the source (None for pageless sources like
        # .txt/.docx), counted on the text itself: chunk attribution would
        # undercount (see rag.count_pages).
        pages = rag.count_pages(text)

        document_id = await db.add_document(
            conversation_id,
            filename,
            pages,
            embedder_name=self.model_file,
            embedding_dim=len(vectors[0]),
            db_path=self.db_path,
        )
        await db.add_chunks(document_id, chunks, db_path=self.db_path)
        return {
            "document_id": document_id,
            "chunks": len(chunks),
            "pages": pages,
            "dim": len(vectors[0]),
        }

    def query_embedder(self) -> Callable[[str], List[float]]:
        """
        The sync embed-one-query callable to hand to the search_documents
        tool. Only valid after ensure_started(): the server's resolved
        port must be known, and the tool (sync, inside the event loop)
        cannot start the server itself — see the module docstring.
        """
        if self._server is None:
            raise RuntimeError(
                "query_embedder() called before ensure_started()"
            )
        from llampaca.tools.documents import make_query_embedder
        return make_query_embedder(self._server.port, self.query_prefix)

    def stop(self) -> None:
        """Stop the embedding server if it is running (idempotent)."""
        if self._server is not None:
            self._server.stop()
            self._server = None
            self._client = None
