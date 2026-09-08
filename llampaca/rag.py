"""
RAG core for large attachments (phase 2, milestone 2): chunking, vector
(de)serialization, and cosine top-k search.

This module is pure logic — no server, no database, no I/O. The pipeline
around it:

    attachments.extract_text()  ->  chunk_text()          (this module)
    LlamaClient.embed()         ->  serialize_vector()    (this module)
    engine.db documents/chunks  ->  storage
    search_documents tool       ->  top_k()               (this module)

Why brute-force numpy instead of a vector index (sqlite-vec, FAISS): a
document produces on the order of 100 chunks (an 80-page thesis ≈ 45k
tokens / 500-token chunks ≈ 90). Exhaustive cosine over a few hundred
1024-dim float32 vectors is a sub-millisecond matrix product — an ANN
index would add a native dependency and tuning surface to speed up
something that is already instant at this scale. The storage format
(one BLOB per chunk) does not preclude moving to sqlite-vec later.
"""

import re
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# Reuse the same chars-per-token heuristic as the agent loop and the
# attachment budget, so every token estimate in the codebase agrees.
# Imported from config (not agent.loop) to avoid an import cycle: this
# module is imported by tools/documents.py, which the agent loop's own
# import chain reaches through the tools package.
from llampaca.config import CHARS_PER_TOKEN

# Chunk sizing, in (estimated) tokens.
#
# 500 is the classic sweet spot for retrieval: big enough that a chunk
# carries a self-contained idea (a few paragraphs), small enough that a
# top-4 result set costs ~2k tokens of context — affordable even at
# --ctx 4096. The 15% overlap repeats the tail of each chunk at the head
# of the next so that a sentence cut by the boundary is still fully
# contained in at least one chunk (otherwise facts sitting exactly on a
# boundary would be unfindable).
CHUNK_TARGET_TOKENS = 500
CHUNK_OVERLAP_TOKENS = 75

# Matches the page markers produced by attachments._extract_pdf(), e.g.
# "--- Page 12 ---" on its own line. Text from other sources (txt, docx)
# has no markers and gets page=None on every chunk.
_PAGE_MARKER = re.compile(r"^--- Page (\d+) ---$", re.MULTILINE)


def chunk_text(text: str) -> List[Dict[str, Any]]:
    """
    Split extracted document text into overlapping retrieval chunks.

    Strategy: split into paragraphs (page markers are consumed and turned
    into page attribution), then greedily pack whole paragraphs into
    chunks of ~CHUNK_TARGET_TOKENS, starting each new chunk with the last
    ~CHUNK_OVERLAP_TOKENS worth of paragraphs from the previous one.
    Paragraph boundaries are preserved because a retrieval hit that starts
    mid-sentence is much harder for the chat model to use. A single
    paragraph longer than a whole chunk (contracts, minified text...) is
    hard-split on whitespace as a fallback.

    Args:
        text: The document text, optionally containing "--- Page N ---"
            markers (as produced by the PDF extractor).

    Returns:
        A list of chunk dicts, in document order:
            {"text": str, "page": int | None, "position": int}
        where "page" is the page the chunk *starts* on (None if the source
        has no page markers) and "position" is the 0-based chunk index.
        Empty/whitespace-only input yields [].
    """
    paragraphs = _paragraphs_with_pages(text)
    if not paragraphs:
        return []

    max_chars = CHUNK_TARGET_TOKENS * CHARS_PER_TOKEN
    overlap_chars = CHUNK_OVERLAP_TOKENS * CHARS_PER_TOKEN

    chunks: List[Dict[str, Any]] = []
    current: List[Tuple[Optional[int], str]] = []  # (page, paragraph)
    current_len = 0

    def close_chunk() -> None:
        """Emit the current paragraph buffer as one chunk."""
        nonlocal current, current_len
        if not current:
            return
        chunks.append({
            "text": "\n\n".join(p for _, p in current),
            "page": current[0][0],
            "position": len(chunks),
        })
        # Seed the next chunk with the tail of this one (the overlap):
        # walk paragraphs backwards until ~overlap_chars are collected.
        tail: List[Tuple[Optional[int], str]] = []
        tail_len = 0
        for page, para in reversed(current):
            if tail_len + len(para) > overlap_chars and tail:
                break
            tail.insert(0, (page, para))
            tail_len += len(para)
        # The overlap must never fill a whole chunk by itself, or packing
        # could stall (every new chunk instantly full of old text).
        if tail_len >= max_chars or tail_len == current_len:
            tail, tail_len = [], 0
        current = tail
        current_len = tail_len

    for page, paragraph in paragraphs:
        # Fallback for degenerate paragraphs longer than a whole chunk:
        # flush what we have, then hard-split the giant on whitespace.
        if len(paragraph) > max_chars:
            close_chunk()
            for piece in _split_long_paragraph(paragraph, max_chars):
                chunks.append({
                    "text": piece,
                    "page": page,
                    "position": len(chunks),
                })
            current, current_len = [], 0
            continue

        if current_len + len(paragraph) > max_chars and current:
            close_chunk()
        current.append((page, paragraph))
        current_len += len(paragraph)

    close_chunk()
    # close_chunk() reseeds `current` with overlap paragraphs; after the
    # final flush that leftover must not become a chunk of its own — it is
    # pure repetition of text already emitted.
    return chunks


def count_pages(text: str) -> Optional[int]:
    """
    The page count of an extracted document, from its "--- Page N ---"
    markers (None for pageless sources). Counted on the SOURCE text, not
    on chunk attribution: a chunk's page is the page it *starts* on, so
    the max over chunks undercounts whenever trailing pages merge into an
    earlier-starting chunk.
    """
    pages = [int(match) for match in _PAGE_MARKER.findall(text)]
    return max(pages) if pages else None


def _paragraphs_with_pages(text: str) -> List[Tuple[Optional[int], str]]:
    """
    Split text into non-empty paragraphs, each tagged with the page it
    belongs to (None when the text has no page markers). Markers are
    consumed: they attribute pages but never appear in chunk text.
    """
    result: List[Tuple[Optional[int], str]] = []
    page: Optional[int] = None
    # split() keeps the captured page numbers at odd indices:
    # [before, "12", segment, "13", segment, ...]
    parts = _PAGE_MARKER.split(text)
    for index, part in enumerate(parts):
        if index % 2 == 1:  # a captured page number
            page = int(part)
            continue
        for paragraph in part.split("\n\n"):
            paragraph = paragraph.strip()
            if paragraph:
                result.append((page, paragraph))
    return result


def _split_long_paragraph(paragraph: str, max_chars: int) -> List[str]:
    """
    Hard-split a paragraph longer than a chunk into <= max_chars pieces,
    cutting on whitespace where possible so words stay intact.
    """
    pieces: List[str] = []
    remaining = paragraph
    while len(remaining) > max_chars:
        cut = remaining.rfind(" ", max_chars // 2, max_chars)
        if cut == -1:  # no whitespace in range (one giant token): cut hard
            cut = max_chars
        pieces.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        pieces.append(remaining)
    return pieces


# ----------------------------------------------------------------------
# Vector (de)serialization — the storage format for the chunks table
# ----------------------------------------------------------------------

def serialize_vector(vector: List[float]) -> bytes:
    """
    Encode an embedding as raw little-endian float32 bytes for a SQLite
    BLOB. float32 halves the storage of Python's float64 and is far more
    precision than cosine ranking needs.
    """
    return np.asarray(vector, dtype="<f4").tobytes()


def deserialize_vector(blob: bytes) -> np.ndarray:
    """Decode a BLOB written by serialize_vector back into a 1-D array."""
    return np.frombuffer(blob, dtype="<f4")


# ----------------------------------------------------------------------
# Search
# ----------------------------------------------------------------------

def top_k(
    query_vector: List[float],
    rows: List[Dict[str, Any]],
    k: int = 4,
) -> List[Dict[str, Any]]:
    """
    Rank chunk rows by cosine similarity to the query and return the best k.

    Args:
        query_vector: The embedded query (already carrying the model's
            query prefix — that happened at embed time).
        rows: Chunk dicts each holding an "embedding" BLOB (as stored in
            the chunks table) plus any metadata (text, page, filename...).
        k: How many results to return (fewer if there are fewer rows).

    Returns:
        Up to k of the input rows — dict copies with a "score" key added —
        sorted by descending similarity. Rows whose embedding dimension
        does not match the query are skipped: mixing embedders must not
        crash the search (the caller re-indexes in that case), and a wrong
        dot product would silently rank garbage.
    """
    if not rows or k <= 0:
        return []

    query = np.asarray(query_vector, dtype="<f4")
    usable = [r for r in rows if len(r["embedding"]) == query.size * 4]
    if not usable:
        return []

    matrix = np.vstack([deserialize_vector(r["embedding"]) for r in usable])

    # Proper cosine: normalize both sides. llama-server usually returns
    # normalized embeddings already, but that is a server detail we must
    # not depend on; the extra normalization is a no-op in that case.
    query_norm = np.linalg.norm(query)
    row_norms = np.linalg.norm(matrix, axis=1)
    # Guard against zero vectors (e.g. an empty chunk embedded by mistake):
    # give them score 0 instead of dividing by zero.
    row_norms[row_norms == 0] = np.inf
    if query_norm == 0:
        return []
    scores = (matrix @ query) / (row_norms * query_norm)

    order = np.argsort(scores)[::-1][:k]
    results = []
    for index in order:
        row = dict(usable[index])
        row["score"] = float(scores[index])
        results.append(row)
    return results
