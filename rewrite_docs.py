import sys

with open("llampaca/tools/documents.py", "r") as f:
    text = f.read()

# I will replace the whole file content basically. Let's just write a new one that merges the docstring.

with open("llampaca/tools/documents.py", "w") as f:
    f.write('''"""
The search_documents agent tool (phase 2, milestone 3).

This is the ONLY model-facing piece of the RAG pipeline: when an
attachment is too large for direct injection, the CLI indexes it
(chunk + embed + store) and registers this tool; the model then queries
the index instead of reading the document from its prompt.
"""

from pathlib import Path
from typing import Optional

from llampaca.engine import db
from llampaca import rag
from llampaca.engine.client import LlamaClient

# Bounds for the model-supplied top_k: at least 1 result, and no more
# than 8 — beyond that the tool result would eat the context budget the
# retrieval exists to protect.
MIN_TOP_K = 1
MAX_TOP_K = 8

def register_document_tools(
    registry,
    embed_client: LlamaClient,
    query_prefix: str,
    conversation_id: str,
    db_path: Optional[Path] = None,
) -> None:
    """
    Register search_documents on an existing registry.
    """

    async def search_documents(query: str, top_k: int = 4) -> str:
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
        rows = await db.get_conversation_chunks(conversation_id, db_path)
        if not rows:
            return (
                "No documents are indexed for this conversation. Tell the "
                "user to attach one with /attach."
            )

        try:
            vectors = await embed_client.embed([query_prefix + query])
            vector = vectors[0]
        except Exception as e:
            return (
                f"Error: could not embed the query (is the embedding server "
                f"running?): {type(e).__name__}: {e}"
            )

        k = max(MIN_TOP_K, min(int(top_k), MAX_TOP_K))
        results = rag.top_k(vector, rows, k=k)
        if not results:
            return (
                "Error: the document index is incompatible with the current "
                "embedding model. Re-attach the document to re-index it."
            )

        blocks = []
        for row in results:
            where = f"[{row['filename']}"
            if row["page"] is not None:
                where += f", p.{row['page']}"
            where += f" | relevance {row['score']:.2f}]"
            blocks.append(f"{where}\\n{row['text']}")
        header = f"Top {len(results)} passages for \\"{query}\\":"
        return header + "\\n\\n" + "\\n\\n".join(blocks)

    registry.register(search_documents, requires_confirmation=False)
''')
