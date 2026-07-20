"""
The search_documents agent tool (phase 2, milestone 3).

This is the ONLY model-facing piece of the RAG pipeline: when an
attachment is too large for direct injection, the CLI indexes it
(chunk + embed + store) and registers this tool; the model then queries
the index instead of reading the document from its prompt.

Design constraints that shaped this module:

- The tool registry executes tools as plain SYNC functions from inside
  the agent's running event loop, so everything here is synchronous:
  the query is embedded with a blocking HTTP call to the local embedding
  server, and chunks are read with the sync sqlite3 helper in engine/db.
  Both calls are local and sub-second — blocking is fine, the agent is
  waiting for the tool result anyway.

- The tool is NOT part of build_default_registry(): it is registered
  dynamically (register_document_tools) only when a conversation actually
  has indexed documents. Small local models call tools more reliably the
  fewer they see, so an always-present search tool with usually-empty
  results would cost accuracy for nothing. After registering, the caller
  must invoke Agent.refresh_tools() so prompt-mode models get the updated
  system prompt.

- Dependencies (how to embed a query, which conversation to search) are
  injected as arguments instead of read from config here, keeping the
  tool unit-testable with fakes and free of server/lifecycle knowledge.
"""

from pathlib import Path
from typing import Callable, List, Optional

from llampaca.engine import db
from llampaca import rag

# Bounds for the model-supplied top_k: at least 1 result, and no more
# than 8 — beyond that the tool result would eat the context budget the
# retrieval exists to protect.
MIN_TOP_K = 1
MAX_TOP_K = 8


def make_query_embedder(
    port: int,
    query_prefix: str,
    timeout: float = 30.0,
) -> Callable[[str], List[float]]:
    """
    Build the sync embed-one-query callable used by search_documents.

    Args:
        port: Port of the running llama-server embedding instance.
        query_prefix: The model-specific instruction prefix for QUERIES
            (see the embedding preset in config.py). Applied here so the
            tool itself never has to know about asymmetric prefixes.
        timeout: HTTP timeout in seconds.
    """
    import requests

    def embed_query(query: str) -> List[float]:
        response = requests.post(
            f"http://127.0.0.1:{port}/v1/embeddings",
            json={"model": "local-embedder", "input": [query_prefix + query]},
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()["data"][0]["embedding"]

    return embed_query


def register_document_tools(
    registry,
    embed_query: Callable[[str], List[float]],
    conversation_id: str,
    db_path: Optional[Path] = None,
) -> None:
    """
    Register search_documents on an existing registry.

    Args:
        registry: The session's ToolRegistry.
        embed_query: Sync callable turning a query string into a vector
            (make_query_embedder in production, a fake in tests).
        conversation_id: The conversation whose indexed documents are
            searchable — the tool never sees other conversations' indexes.
        db_path: Database override for tests; None uses the app database.
    """

    def search_documents(query: str, top_k: int = 4) -> str:
        """
        Search inside the documents attached to this conversation and
        return the most relevant passages. The documents are NOT in your
        context: this tool is the only way to read them.

        Args:
            query: What to look for, phrased as a full question or
                statement (e.g. "termination notice period of the lease"),
                in the language of the document when possible.
            top_k: How many passages to return (1-8, default 4). Ask for
                more only when the first results were not enough.
        """
        # The chunks are re-read from the database on every call (instead
        # of being cached at registration time) so documents attached
        # later in the session are immediately searchable.
        rows = db.get_conversation_chunks_sync(conversation_id, db_path)
        if not rows:
            return (
                "No documents are indexed for this conversation. Tell the "
                "user to attach one with /attach."
            )

        try:
            vector = embed_query(query)
        except Exception as e:
            # The embedding server being down must read as a tool error the
            # model can relay, not as a crash.
            return (
                f"Error: could not embed the query (is the embedding server "
                f"running?): {type(e).__name__}: {e}"
            )

        k = max(MIN_TOP_K, min(int(top_k), MAX_TOP_K))
        results = rag.top_k(vector, rows, k=k)
        if not results:
            # rows existed but none were usable: in practice a dimension
            # mismatch (index built by a different embedder). Milestone 4
            # re-indexes on mismatch; meanwhile the message says what's up.
            return (
                "Error: the document index is incompatible with the current "
                "embedding model. Re-attach the document to re-index it."
            )

        # One block per hit. The [file, page | relevance] header keeps the
        # provenance visible so the model can cite it ("on page 3 of X...").
        blocks = []
        for row in results:
            where = f"[{row['filename']}"
            if row["page"] is not None:
                where += f", p.{row['page']}"
            where += f" | relevance {row['score']:.2f}]"
            blocks.append(f"{where}\n{row['text']}")
        header = f"Top {len(results)} passages for \"{query}\":"
        return header + "\n\n" + "\n\n".join(blocks)

    registry.register(search_documents, requires_confirmation=False)
