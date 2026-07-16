"""
Tests for the milestone-1 embedding infrastructure (phase 2, RAG):
the embedding preset, LlamaServer's embedding mode, and LlamaClient.embed.

No llama-server is launched here: the server tests only inspect the
generated command line, and the client test replaces the OpenAI embeddings
endpoint with a fake. The real end-to-end check (start the embedding
server, embed sentences, verify cosine ordering) is done live via the
verify skill, not in unit tests.
"""

import unittest
from pathlib import Path

from llampaca.config import MODEL_PRESETS, DEFAULT_CONFIG, get_preset_for_file
from llampaca.engine.server import LlamaServer
from llampaca.engine.client import LlamaClient


class TestEmbeddingPreset(unittest.TestCase):
    def test_embedding_preset_exists_and_is_complete(self):
        preset = MODEL_PRESETS["qwen3-embedding-0.6b"]
        self.assertEqual(preset["kind"], "embedding")
        # The RAG pipeline depends on these keys existing for any
        # embedding preset; a missing one would silently degrade retrieval
        # (prefixes) or produce garbage vectors (pooling).
        self.assertIn("pooling", preset)
        self.assertIn("query_prefix", preset)
        self.assertIn("document_prefix", preset)
        self.assertTrue(preset["file"].endswith(".gguf"))

    def test_chat_presets_have_no_embedding_kind(self):
        for name, preset in MODEL_PRESETS.items():
            if name == "qwen3-embedding-0.6b":
                continue
            self.assertNotEqual(preset.get("kind"), "embedding", name)

    def test_default_config_has_embedding_keys(self):
        self.assertIn("embedding_model", DEFAULT_CONFIG)
        self.assertIn("embedding_port", DEFAULT_CONFIG)
        # Separate port range: must not collide with the chat default,
        # or the auto-increment scans of the two servers would race.
        self.assertNotEqual(
            DEFAULT_CONFIG["embedding_port"], DEFAULT_CONFIG["server_port"]
        )

    def test_get_preset_for_file_reverse_lookup(self):
        preset = get_preset_for_file("Qwen3-Embedding-0.6B-Q8_0.gguf")
        self.assertIsNotNone(preset)
        self.assertEqual(preset["kind"], "embedding")
        self.assertIsNone(get_preset_for_file("no-such-file.gguf"))


class TestServerCommand(unittest.TestCase):
    """Inspect the command line built for each server role."""

    def _cmd(self, **kwargs) -> list:
        server = LlamaServer(model_path=Path("/tmp/fake.gguf"), **kwargs)
        return server._build_command()

    def test_chat_mode_flags(self):
        cmd = self._cmd(port=9000)
        self.assertIn("--jinja", cmd)
        self.assertIn("--cache-reuse", cmd)
        self.assertNotIn("--embedding", cmd)
        self.assertNotIn("--pooling", cmd)

    def test_embedding_mode_flags(self):
        cmd = self._cmd(embedding=True, pooling="last", port=9100)
        self.assertIn("--embedding", cmd)
        # Pooling must reach the command line: wrong/missing pooling
        # produces meaningless vectors without any error.
        self.assertEqual(cmd[cmd.index("--pooling") + 1], "last")
        # Chunks must fit one physical batch.
        self.assertIn("--ubatch-size", cmd)
        # None of the chat-only flags belong in embedding mode.
        for flag in ("--jinja", "--cache-reuse", "-fa", "-ctk", "-ctv"):
            self.assertNotIn(flag, cmd)

    def test_embedding_mode_defaults(self):
        server = LlamaServer(model_path=Path("/tmp/fake.gguf"), embedding=True)
        # Own port range and small dedicated context (chunks are ~500 tok).
        self.assertEqual(server.context_size, 2048)
        self.assertNotEqual(server.port, DEFAULT_CONFIG["server_port"])
        # Distinct pid/log names so both servers can track their own files,
        # while still matching the cleanup_orphans() "llama-server-*" glob.
        self.assertIn("llama-server-embed", server.pid_file_path.name)
        self.assertTrue(server.pid_file_path.name.startswith("llama-server-"))

    def test_pooling_is_configurable(self):
        cmd = self._cmd(embedding=True, pooling="mean", port=9100)
        self.assertEqual(cmd[cmd.index("--pooling") + 1], "mean")


class TestClientEmbed(unittest.IsolatedAsyncioTestCase):
    """LlamaClient.embed: batching and order guarantees, with a fake API."""

    def _client_with_fake(self, dim=4):
        client = LlamaClient(port=1)  # never contacted: endpoint is faked
        calls = []

        class _Item:
            def __init__(self, index, embedding):
                self.index = index
                self.embedding = embedding

        class _Response:
            def __init__(self, items):
                self.data = items

        async def fake_create(model, input):
            calls.append(list(input))
            # Reply out of order on purpose: embed() must re-sort by index.
            items = [
                _Item(i, [float(hash(text) % 97)] * dim)
                for i, text in enumerate(input)
            ]
            return _Response(list(reversed(items)))

        client.client.embeddings.create = fake_create
        return client, calls

    async def test_order_is_preserved(self):
        client, _ = self._client_with_fake()
        texts = ["alpha", "beta", "gamma"]
        vectors = await client.embed(texts)
        self.assertEqual(len(vectors), 3)
        # Each vector must correspond to its own text despite the fake
        # server replying in reverse order.
        for text, vector in zip(texts, vectors):
            self.assertEqual(vector[0], float(hash(text) % 97))

    async def test_batching_slices_requests(self):
        client, calls = self._client_with_fake()
        texts = [f"chunk {i}" for i in range(70)]
        vectors = await client.embed(texts, batch_size=32)
        self.assertEqual(len(vectors), 70)
        # 70 texts at batch_size 32 -> 32 + 32 + 6
        self.assertEqual([len(c) for c in calls], [32, 32, 6])


class TestEmbeddingService(unittest.IsolatedAsyncioTestCase):
    """
    EmbeddingService (milestone 4): resolution errors, lifecycle guards,
    and indexing against a faked embedding client + a temp database.
    """

    async def asyncSetUp(self):
        import os
        import tempfile
        from llampaca.engine.db import init_db, create_conversation

        self._fd, temp_path = tempfile.mkstemp(suffix=".db")
        self.db_path = Path(temp_path)
        await init_db(self.db_path)
        self.conv_id = await create_conversation(
            model_name="m", db_path=self.db_path
        )
        self._os = os

    async def asyncTearDown(self):
        self._os.close(self._fd)
        if self.db_path.exists():
            self.db_path.unlink()

    def _service_with_fake_server(self, dim=8):
        """A service faked into the 'started' state: no real server."""
        from types import SimpleNamespace
        from llampaca.engine.embedding import EmbeddingService

        service = EmbeddingService(db_path=self.db_path)
        service.document_prefix = "DOC: "
        service._server = SimpleNamespace(port=1, stop=lambda: None)

        embedded = []

        class FakeClient:
            async def embed(self, texts, **kwargs):
                embedded.extend(texts)
                return [[float(len(t))] * dim for t in texts]

        service._client = FakeClient()
        return service, embedded

    async def test_missing_model_raises_unavailable(self):
        from llampaca.engine.embedding import (
            EmbeddingService, EmbeddingUnavailable,
        )
        service = EmbeddingService(db_path=self.db_path)
        service.model_file = "does-not-exist.gguf"
        with self.assertRaises(EmbeddingUnavailable) as ctx:
            await service.ensure_started()
        # The message must carry the fix, not just the problem.
        self.assertIn("models download", str(ctx.exception))

    async def test_query_embedder_requires_started_server(self):
        from llampaca.engine.embedding import EmbeddingService
        service = EmbeddingService(db_path=self.db_path)
        with self.assertRaises(RuntimeError):
            service.query_embedder()

    async def test_index_document_roundtrip(self):
        from llampaca.engine.db import list_documents, get_conversation_chunks

        service, embedded = self._service_with_fake_server()
        service.model_file = "fake-embedder.gguf"
        text = (
            "--- Page 1 ---\nPrimo paragrafo del documento.\n\n"
            "--- Page 2 ---\nSecondo paragrafo, altra pagina."
        )
        info = await service.index_document(self.conv_id, "doc.pdf", text)

        # Info block feeds the CLI's [indexed] line.
        self.assertEqual(info["chunks"], 1)  # tiny text: one chunk
        self.assertEqual(info["pages"], 2)
        self.assertEqual(info["dim"], 8)

        # The document prefix must reach the embedder.
        self.assertTrue(embedded[0].startswith("DOC: "))

        # Stored: document metadata records the embedder for the
        # compatibility check, chunks carry the vectors.
        docs = await list_documents(self.conv_id, db_path=self.db_path)
        self.assertEqual(docs[0]["embedder_name"], "fake-embedder.gguf")
        self.assertEqual(docs[0]["embedding_dim"], 8)
        rows = await get_conversation_chunks(self.conv_id, db_path=self.db_path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]["embedding"]), 8 * 4)  # float32

    async def test_stop_is_idempotent_without_start(self):
        from llampaca.engine.embedding import EmbeddingService
        service = EmbeddingService(db_path=self.db_path)
        service.stop()  # never started: must be a silent no-op
        self.assertFalse(service.started)


if __name__ == "__main__":
    unittest.main()
