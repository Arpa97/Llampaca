"""
Tests for milestone 3: the search_documents tool (tools/documents.py) and
the Agent's dynamic tool activation (refresh_tools / the system-prompt
rebuild in agent/loop.py). No servers involved: embedding is faked, the
database is a temp file, and the Agent gets a fake client.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

from llampaca.agent.loop import Agent
from llampaca.engine.db import (
    init_db,
    create_conversation,
    add_document,
    add_chunks,
)
from llampaca.rag import serialize_vector
from llampaca.tools import ToolRegistry, register_document_tools


class FakeEmbedClient:
    """
    Adapts the simple `query -> vector` callables these tests use to the
    LlamaClient interface register_document_tools now expects: an object with
    an async `embed(texts) -> vectors`. The signature changed when embedding
    moved behind EmbeddingService; the tool takes the client and the query
    prefix instead of a pre-bound embedding function.
    """

    def __init__(self, embed_query):
        self._embed_query = embed_query

    async def embed(self, texts):
        return [self._embed_query(text) for text in texts]


class FakeClient:
    """Just enough of LlamaClient for Agent.__init__'s template probe."""

    def __init__(self, template: str):
        self._template = template

    def get_chat_template(self) -> str:
        return self._template


def _dummy_tool(text: str) -> str:
    """Echo a string back.

    Args:
        text: The text to echo.
    """
    return text


class TestSearchDocumentsTool(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_db_fd, temp_path = tempfile.mkstemp(suffix=".db")
        self.db_path = Path(temp_path)
        await init_db(self.db_path)
        self.conv_id = await create_conversation(
            model_name="test-model", db_path=self.db_path
        )
        # Three chunks along distinct axes: a query vector equal to one
        # axis must rank its chunk first with score 1.0.
        doc_id = await add_document(
            self.conv_id, "guida.pdf", pages=3,
            embedder_name="fake.gguf", embedding_dim=3, db_path=self.db_path,
        )
        await add_chunks(doc_id, [
            {"text": "capitolo ascensore", "page": 1, "position": 0,
             "embedding": serialize_vector([1.0, 0.0, 0.0])},
            {"text": "capitolo giardino", "page": 2, "position": 1,
             "embedding": serialize_vector([0.0, 1.0, 0.0])},
            {"text": "capitolo parcheggio", "page": 3, "position": 2,
             "embedding": serialize_vector([0.0, 0.0, 1.0])},
        ], db_path=self.db_path)

    async def asyncTearDown(self):
        os.close(self.temp_db_fd)
        if self.db_path.exists():
            self.db_path.unlink()

    def _registry(self, embed_query=lambda q: [1.0, 0.0, 0.0],
                  conversation_id=None):
        registry = ToolRegistry()
        register_document_tools(
            registry,
            embed_client=FakeEmbedClient(embed_query),
            query_prefix="",
            conversation_id=conversation_id or self.conv_id,
            db_path=self.db_path,
        )
        return registry

    def test_schema_generation(self):
        registry = self._registry()
        definition = registry.definitions()[0]["function"]
        self.assertEqual(definition["name"], "search_documents")
        # query is mandatory, top_k optional with integer type
        self.assertEqual(definition["parameters"]["required"], ["query"])
        self.assertEqual(
            definition["parameters"]["properties"]["top_k"]["type"], "integer"
        )
        # The description must tell the model the docs are NOT in context.
        self.assertIn("NOT in your context", definition["description"])

    async def test_search_returns_formatted_hits(self):
        registry = self._registry()
        result = await registry.execute(
            "search_documents", json.dumps({"query": "ascensore", "top_k": 2})
        )
        # Best hit first, with provenance header and relevance score.
        self.assertIn("[guida.pdf, p.1 | relevance 1.00]", result)
        self.assertIn("capitolo ascensore", result)
        # top_k respected: 2 blocks, so exactly two headers.
        self.assertEqual(result.count("[guida.pdf"), 2)

    async def test_top_k_is_clamped(self):
        registry = self._registry()
        result = await registry.execute(
            "search_documents", json.dumps({"query": "x", "top_k": 100})
        )
        # 100 clamps to 8, and only 3 chunks exist anyway.
        self.assertEqual(result.count("[guida.pdf"), 3)

    async def test_no_documents_message(self):
        other_conv = await create_conversation(
            model_name="test-model", db_path=self.db_path
        )
        registry = self._registry(conversation_id=other_conv)
        result = await registry.execute("search_documents", json.dumps({"query": "x"}))
        self.assertIn("No documents are indexed", result)
        self.assertIn("/attach", result)

    async def test_embed_failure_is_a_tool_error(self):
        def broken(_query):
            raise ConnectionError("connection refused")
        registry = self._registry(embed_query=broken)
        result = await registry.execute("search_documents", json.dumps({"query": "x"}))
        self.assertIn("could not embed", result)
        self.assertIn("connection refused", result)

    async def test_dimension_mismatch_says_reindex(self):
        # Query embedded in 5 dims against a 3-dim index: incompatible.
        registry = self._registry(embed_query=lambda q: [1.0] * 5)
        result = await registry.execute("search_documents", json.dumps({"query": "x"}))
        self.assertIn("incompatible", result)
        self.assertIn("Re-attach", result)


class TestRefreshTools(unittest.TestCase):
    """Dynamic tool activation in both tool modes."""

    TOOL_SECTION_MARKER = "You have access to the following tools"

    def _prompt_mode_agent(self, registry):
        # A template that never mentions tools -> prompt-based mode.
        return Agent(client=FakeClient("{{ gemma-ish template }}"),
                     registry=registry)

    def test_prompt_mode_refresh_adds_new_tool(self):
        registry = ToolRegistry()
        registry.register(_dummy_tool)
        agent = self._prompt_mode_agent(registry)
        self.assertFalse(agent.native_tools)
        self.assertIn("_dummy_tool", agent.messages[0]["content"])
        self.assertNotIn("search_documents", agent.messages[0]["content"])

        # A document gets indexed mid-session: the tool joins the registry.
        registry.register(lambda query: "", name="search_documents",
                          description="search")
        agent.refresh_tools()
        self.assertIn("search_documents", agent.messages[0]["content"])

    def test_refresh_is_idempotent(self):
        registry = ToolRegistry()
        registry.register(_dummy_tool)
        agent = self._prompt_mode_agent(registry)
        agent.refresh_tools()
        agent.refresh_tools()
        content = agent.messages[0]["content"]
        # Exactly one tool section and one date line: rebuild, not append.
        self.assertEqual(content.count(self.TOOL_SECTION_MARKER), 1)
        self.assertEqual(content.count("Today's date is"), 1)

    def test_native_mode_refresh_leaves_prompt_alone(self):
        registry = ToolRegistry()
        registry.register(_dummy_tool)
        # Template mentions tools -> native mode: definitions travel as a
        # request parameter, the system prompt must stay tool-free.
        agent = Agent(client=FakeClient("{% if tools %}...{% endif %}"),
                      registry=registry)
        self.assertTrue(agent.native_tools)
        before = agent.messages[0]["content"]
        self.assertNotIn(self.TOOL_SECTION_MARKER, before)
        registry.register(lambda query: "", name="search_documents",
                          description="search")
        agent.refresh_tools()
        self.assertEqual(agent.messages[0]["content"], before)

    def test_switch_to_prompt_mode_is_idempotent_and_composes(self):
        registry = ToolRegistry()
        registry.register(_dummy_tool)
        agent = Agent(client=FakeClient("{% if tools %}...{% endif %}"),
                      registry=registry)
        # Runtime fallback fires (possibly more than once, defensively):
        agent._switch_to_prompt_mode()
        agent._switch_to_prompt_mode()
        content = agent.messages[0]["content"]
        self.assertEqual(content.count(self.TOOL_SECTION_MARKER), 1)
        # ...and a later refresh after adding a tool still yields one section.
        registry.register(lambda query: "", name="search_documents",
                          description="search")
        agent.refresh_tools()
        content = agent.messages[0]["content"]
        self.assertEqual(content.count(self.TOOL_SECTION_MARKER), 1)
        self.assertIn("search_documents", content)

    def test_refresh_updates_tools_enabled(self):
        registry = ToolRegistry()
        agent = self._prompt_mode_agent(registry)
        # Empty registry: agent starts as a plain chatbot.
        self.assertFalse(agent.tools_enabled)
        registry.register(_dummy_tool)
        agent.refresh_tools()
        self.assertTrue(agent.tools_enabled)


if __name__ == "__main__":
    unittest.main()
