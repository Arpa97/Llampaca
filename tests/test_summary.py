import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import tempfile
import os
from pathlib import Path

# Add project root to sys.path so we can import llampaca
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from llampaca.agent.loop import Agent
# MIN_ACTIVE_WINDOW lives in agent/context.py since the history-trimming
# logic was split out of the agent loop.
from llampaca.agent.context import MIN_ACTIVE_WINDOW, estimate_tokens
from llampaca.engine.client import LlamaClient
from llampaca.engine.db import (
    init_db,
    create_conversation,
    add_message,
    get_conversation,
    update_conversation_summary
)

class TestSummaryAndTrimming(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        """Create a temporary database file for each test."""
        self.temp_db_fd, self.temp_db_path = tempfile.mkstemp(suffix=".db")
        self.db_path = Path(self.temp_db_path)
        await init_db(self.db_path)

        # Mock the client
        self.mock_client = MagicMock(spec=LlamaClient)
        # Mock self.client.client.chat.completions.create
        self.mock_openai_client = AsyncMock()
        self.mock_client.client = self.mock_openai_client
        self.mock_client.get_chat_template.return_value = "native"

    async def asyncTearDown(self):
        """Clean up the temporary database file after each test."""
        os.close(self.temp_db_fd)
        if self.db_path.exists():
            self.db_path.unlink()

    async def test_db_summary_columns(self):
        """Test that the conversations table has summary columns."""
        conv_id = await create_conversation(model_name="test-model", db_path=self.db_path)
        conv = await get_conversation(conv_id, db_path=self.db_path)
        
        # Verify the columns exist and default to None
        self.assertIn("summary", conv)
        self.assertIn("last_summarized_message_id", conv)
        self.assertIsNone(conv["summary"])
        self.assertIsNone(conv["last_summarized_message_id"])

        # Test updating summary
        await update_conversation_summary(
            conv_id,
            summary="This is a summary.",
            last_summarized_message_id=42,
            db_path=self.db_path
        )
        
        updated = await get_conversation(conv_id, db_path=self.db_path)
        self.assertEqual(updated["summary"], "This is a summary.")
        self.assertEqual(updated["last_summarized_message_id"], 42)

    async def test_trim_history_called(self):
        """Test that _trim_history trims when exceeding the threshold and queues
        the removed turns, and that summarize_pending() then generates and
        returns the summary (deferred summarization)."""
        # Create an Agent with a small context_size to force trimming
        agent = Agent(
            client=self.mock_client,
            context_size=500,  # very small context size
            summary=None
        )

        # Add some messages with mock IDs to simulate history
        agent.messages = [
            {"role": "system", "content": "You are Llampaca."},
            {"role": "user", "content": "Hello " * 100, "id": 1},  # ~100 tokens
            {"role": "assistant", "content": "Hi " * 100, "id": 2},   # ~100 tokens
            {"role": "user", "content": "Question " * 100, "id": 3},  # ~100 tokens
            {"role": "assistant", "content": "Answer " * 100, "id": 4},  # ~100 tokens
            {"role": "user", "content": "More question 1", "id": 5},
            {"role": "assistant", "content": "More answer 1", "id": 6},
            {"role": "user", "content": "More question 2", "id": 7},
            {"role": "assistant", "content": "More answer 2", "id": 8},
        ]
        
        # Ensure the token estimate exceeds high_budget (500 * 0.8 = 400).
        # Agent._estimate_tokens() became the free function estimate_tokens()
        # in agent/context.py when the trimming logic was split out.
        estimated = estimate_tokens(
            agent.messages, agent.tools_enabled, agent.native_tools, agent.registry
        )
        self.assertGreater(estimated, 400)

        # Mock the chat.completions.create response for summarization
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "Aggregated summary of conversation."
        mock_response.choices = [mock_choice]
        self.mock_openai_client.chat.completions.create.return_value = mock_response

        # Run trimming (now synchronous and trim-only: summarization is
        # deferred to summarize_pending, off the turn's critical path)
        dropped = agent._trim_history()

        # Check that trimming occurred and the removed turns were queued,
        # but no summary was generated yet (no LLM call on the hot path)
        self.assertGreater(dropped, 0)
        self.assertTrue(agent.has_pending_summary)
        self.assertIsNone(agent.summary)
        self.mock_openai_client.chat.completions.create.assert_not_called()

        # Drain the queue: this is where the LLM call happens
        data = await agent.summarize_pending()

        self.assertEqual(data["summary"], "Aggregated summary of conversation.")
        # The last_summarized_message_id should be the ID of the last trimmed message
        self.assertEqual(data["last_summarized_message_id"], 2)  # because system is not popped, messages are popped from start (index 1) until under low_budget (500 * 0.6 = 300)
        self.assertEqual(agent.summary, "Aggregated summary of conversation.")
        # The queue is drained: a second flush has nothing to do
        self.assertFalse(agent.has_pending_summary)
        self.assertIsNone(await agent.summarize_pending())

        # Ensure MIN_ACTIVE_WINDOW (6) was respected:
        # system prompt + at least 6 messages must remain in agent.messages
        self.assertGreaterEqual(len(agent.messages), 1 + MIN_ACTIVE_WINDOW)
