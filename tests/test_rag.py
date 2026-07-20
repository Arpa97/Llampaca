"""
Tests for the milestone-2 RAG core: chunking (llampaca/rag.py), vector
(de)serialization, cosine top-k search, and the documents/chunks tables
in engine/db.py. Everything here is offline — no llama-server involved.
"""

import os
import tempfile
import unittest
from pathlib import Path

import numpy as np

from llampaca.agent.loop import CHARS_PER_TOKEN
from llampaca.rag import (
    CHUNK_OVERLAP_TOKENS,
    CHUNK_TARGET_TOKENS,
    chunk_text,
    deserialize_vector,
    serialize_vector,
    top_k,
)
from llampaca.engine.db import (
    init_db,
    create_conversation,
    delete_conversation,
    add_document,
    add_chunks,
    list_documents,
    get_conversation_chunks,
    delete_document,
)

MAX_CHARS = CHUNK_TARGET_TOKENS * CHARS_PER_TOKEN
OVERLAP_CHARS = CHUNK_OVERLAP_TOKENS * CHARS_PER_TOKEN


def _paragraph(seed: str, chars: int) -> str:
    """A distinctive paragraph of roughly `chars` characters."""
    word = f"{seed}word"
    count = max(1, chars // (len(word) + 1))
    return " ".join(f"{word}{i}" for i in range(count))


class TestChunker(unittest.TestCase):
    def test_empty_input_gives_no_chunks(self):
        self.assertEqual(chunk_text(""), [])
        self.assertEqual(chunk_text("   \n\n  "), [])

    def test_short_text_is_one_chunk(self):
        chunks = chunk_text("Hello.\n\nA second paragraph.")
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["text"], "Hello.\n\nA second paragraph.")
        self.assertIsNone(chunks[0]["page"])
        self.assertEqual(chunks[0]["position"], 0)

    def test_long_text_splits_with_bounded_chunks(self):
        # 20 distinctive paragraphs of ~600 chars -> must split into
        # several chunks, each within the size bound (target + overlap
        # slack: a chunk may carry the overlap tail on top of new text).
        text = "\n\n".join(_paragraph(f"p{i}", 600) for i in range(20))
        chunks = chunk_text(text)
        self.assertGreater(len(chunks), 2)
        for chunk in chunks:
            self.assertLessEqual(len(chunk["text"]), MAX_CHARS + OVERLAP_CHARS + 600)
        # positions are the chunk order
        self.assertEqual([c["position"] for c in chunks], list(range(len(chunks))))
        # Nothing lost: every paragraph's marker word appears somewhere.
        joined = " ".join(c["text"] for c in chunks)
        for i in range(20):
            self.assertIn(f"p{i}word0", joined)

    def test_overlap_repeats_tail_of_previous_chunk(self):
        text = "\n\n".join(_paragraph(f"p{i}", 600) for i in range(20))
        chunks = chunk_text(text)
        for prev, nxt in zip(chunks, chunks[1:]):
            # The next chunk must start with text already seen at the end
            # of the previous one (the overlap seed): its first paragraph
            # is contained in the previous chunk.
            first_paragraph = nxt["text"].split("\n\n")[0]
            self.assertIn(first_paragraph, prev["text"])

    def test_page_markers_attribute_pages_and_are_consumed(self):
        text = (
            "--- Page 1 ---\nIntro paragraph.\n\n"
            "--- Page 2 ---\nSecond page text.\n\n"
            "--- Page 3 ---\nThird page text."
        )
        chunks = chunk_text(text)
        self.assertEqual(len(chunks), 1)  # tiny text: one chunk
        self.assertEqual(chunks[0]["page"], 1)  # page where the chunk starts
        self.assertNotIn("--- Page", chunks[0]["text"])  # markers consumed

    def test_pages_across_multiple_chunks(self):
        # One big paragraph per page so each chunk starts on a known page.
        text = "\n\n".join(
            f"--- Page {n} ---\n{_paragraph(f'page{n}', 1500)}"
            for n in range(1, 7)
        )
        chunks = chunk_text(text)
        self.assertGreater(len(chunks), 1)
        # First chunk starts on page 1; later chunks on increasing pages.
        self.assertEqual(chunks[0]["page"], 1)
        pages = [c["page"] for c in chunks]
        self.assertEqual(pages, sorted(pages))
        self.assertGreater(pages[-1], 1)

    def test_giant_paragraph_is_hard_split(self):
        # A single paragraph 3x the chunk size (no double newlines at all).
        text = _paragraph("giant", MAX_CHARS * 3)
        chunks = chunk_text(text)
        self.assertGreaterEqual(len(chunks), 3)
        for chunk in chunks:
            self.assertLessEqual(len(chunk["text"]), MAX_CHARS)
            # Whitespace split: no word is cut in half (every piece ends
            # at a word boundary, i.e. its last token is a whole word).
            self.assertTrue(chunk["text"][-1] != " ")


class TestVectorSerialization(unittest.TestCase):
    def test_roundtrip(self):
        vector = [0.1, -2.5, 3.25, 0.0, 1e-7]
        blob = serialize_vector(vector)
        self.assertIsInstance(blob, bytes)
        self.assertEqual(len(blob), len(vector) * 4)  # float32
        restored = deserialize_vector(blob)
        np.testing.assert_allclose(restored, vector, rtol=1e-6)


class TestTopK(unittest.TestCase):
    def _rows(self, vectors):
        return [
            {"text": f"chunk {i}", "embedding": serialize_vector(v)}
            for i, v in enumerate(vectors)
        ]

    def test_ranks_by_cosine(self):
        rows = self._rows([[1, 0, 0], [0, 1, 0], [0.9, 0.1, 0]])
        results = top_k([1.0, 0.0, 0.0], rows, k=2)
        self.assertEqual([r["text"] for r in results], ["chunk 0", "chunk 2"])
        self.assertAlmostEqual(results[0]["score"], 1.0, places=5)
        # scores are descending
        self.assertGreaterEqual(results[0]["score"], results[1]["score"])

    def test_cosine_ignores_magnitude(self):
        # Same direction, wildly different norms: must score identically.
        rows = self._rows([[100, 0], [0.001, 0]])
        results = top_k([1.0, 0.0], rows, k=2)
        self.assertAlmostEqual(results[0]["score"], results[1]["score"], places=5)

    def test_k_larger_than_rows(self):
        rows = self._rows([[1, 0]])
        self.assertEqual(len(top_k([1.0, 0.0], rows, k=10)), 1)

    def test_empty_and_zero_edge_cases(self):
        self.assertEqual(top_k([1.0, 0.0], [], k=4), [])
        self.assertEqual(top_k([1.0, 0.0], self._rows([[1, 0]]), k=0), [])
        # zero query vector: no meaningful ranking, empty result
        self.assertEqual(top_k([0.0, 0.0], self._rows([[1, 0]]), k=4), [])
        # zero chunk vector: score 0, not a crash
        results = top_k([1.0, 0.0], self._rows([[0, 0], [1, 0]]), k=2)
        self.assertEqual(results[0]["text"], "chunk 1")
        self.assertEqual(results[1]["score"], 0.0)

    def test_dimension_mismatch_rows_are_skipped(self):
        # A row from a different embedder (wrong dim) must be ignored,
        # not crash the search or pollute the ranking.
        rows = self._rows([[1, 0, 0]]) + self._rows([[1, 0]])
        results = top_k([1.0, 0.0, 0.0], rows, k=4)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["embedding"], serialize_vector([1, 0, 0]))


class TestDocumentTables(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_db_fd, temp_path = tempfile.mkstemp(suffix=".db")
        self.db_path = Path(temp_path)
        await init_db(self.db_path)
        self.conv_id = await create_conversation(
            model_name="test-model", db_path=self.db_path
        )

    async def asyncTearDown(self):
        os.close(self.temp_db_fd)
        if self.db_path.exists():
            self.db_path.unlink()

    async def _index_fake_document(self, filename="doc.pdf", n_chunks=3):
        doc_id = await add_document(
            self.conv_id, filename, pages=10,
            embedder_name="test-embedder.gguf", embedding_dim=4,
            db_path=self.db_path,
        )
        chunks = [
            {
                "text": f"{filename} chunk {i}",
                "page": i + 1,
                "position": i,
                "embedding": serialize_vector([float(i), 0.0, 0.0, 1.0]),
            }
            for i in range(n_chunks)
        ]
        await add_chunks(doc_id, chunks, db_path=self.db_path)
        return doc_id

    async def test_document_and_chunks_roundtrip(self):
        await self._index_fake_document()

        docs = await list_documents(self.conv_id, db_path=self.db_path)
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0]["filename"], "doc.pdf")
        self.assertEqual(docs[0]["embedder_name"], "test-embedder.gguf")
        self.assertEqual(docs[0]["embedding_dim"], 4)

        rows = await get_conversation_chunks(self.conv_id, db_path=self.db_path)
        self.assertEqual(len(rows), 3)
        # order: by document then position
        self.assertEqual([r["position"] for r in rows], [0, 1, 2])
        # filename joined in, BLOB intact through storage
        self.assertEqual(rows[0]["filename"], "doc.pdf")
        np.testing.assert_allclose(
            deserialize_vector(rows[1]["embedding"]), [1.0, 0.0, 0.0, 1.0]
        )
        # stored rows work directly as top_k input
        results = top_k([2.0, 0.0, 0.0, 1.0], rows, k=1)
        self.assertEqual(results[0]["text"], "doc.pdf chunk 2")

    async def test_chunks_scoped_to_conversation(self):
        await self._index_fake_document()
        other_conv = await create_conversation(
            model_name="test-model", db_path=self.db_path
        )
        rows = await get_conversation_chunks(other_conv, db_path=self.db_path)
        self.assertEqual(rows, [])

    async def test_delete_document_cascades_to_chunks(self):
        doc_id = await self._index_fake_document()
        await delete_document(doc_id, db_path=self.db_path)
        self.assertEqual(
            await list_documents(self.conv_id, db_path=self.db_path), []
        )
        self.assertEqual(
            await get_conversation_chunks(self.conv_id, db_path=self.db_path), []
        )

    async def test_delete_conversation_cascades_to_index(self):
        await self._index_fake_document()
        await self._index_fake_document(filename="second.docx")
        await delete_conversation(self.conv_id, db_path=self.db_path)
        # Both documents and all their chunks must be gone with the chat —
        # the whole reason the index lives in history.db.
        self.assertEqual(
            await get_conversation_chunks(self.conv_id, db_path=self.db_path), []
        )


if __name__ == "__main__":
    unittest.main()
