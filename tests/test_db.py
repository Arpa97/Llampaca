import os
import unittest
import tempfile
import asyncio
from pathlib import Path

# Add the project root to sys.path so we can import llampaca
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from llampaca.engine.db import (
    init_db,
    create_conversation,
    add_message,
    get_conversation,
    list_conversations,
    delete_conversation,
    update_conversation_title
)

class TestDatabase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        """Create a temporary database file for each test."""
        self.temp_db_fd, self.temp_db_path = tempfile.mkstemp(suffix=".db")
        self.db_path = Path(self.temp_db_path)
        await init_db(self.db_path)

    async def asyncTearDown(self):
        """Clean up the temporary database file after each test."""
        os.close(self.temp_db_fd)
        if self.db_path.exists():
            self.db_path.unlink()

    async def test_create_and_get_conversation(self):
        """Test creating a conversation and retrieving its metadata."""
        model_name = "qwen3.5-4b-instruct"
        conv_id = await create_conversation(model_name=model_name, title="Custom Title", db_path=self.db_path)
        
        # Verify the ID is a valid string/UUID
        self.assertTrue(isinstance(conv_id, str))
        self.assertGreater(len(conv_id), 0)
        
        # Fetch the conversation and assert correctness
        conv = await get_conversation(conv_id, db_path=self.db_path)
        self.assertIsNotNone(conv)
        self.assertEqual(conv["id"], conv_id)
        self.assertEqual(conv["title"], "Custom Title")
        self.assertEqual(conv["model_name"], model_name)
        self.assertEqual(len(conv["messages"]), 0)

    async def test_message_cascade_and_auto_titling(self):
        """Test adding messages, checking cascade deletion, and auto-titling logic."""
        conv_id = await create_conversation(model_name="test-model", db_path=self.db_path)
        
        # Add system prompt
        await add_message(conv_id, "system", "You are an assistant.", db_path=self.db_path)
        
        # Verify title remains default "New Conversation"
        conv = await get_conversation(conv_id, db_path=self.db_path)
        self.assertEqual(conv["title"], "New Conversation")
        self.assertEqual(len(conv["messages"]), 1)
        self.assertEqual(conv["messages"][0]["role"], "system")
        self.assertEqual(conv["messages"][0]["content"], "You are an assistant.")
        
        # Add first user message, should trigger auto-titling
        user_msg = "Hello, how do I make sourdough bread at home?"
        await add_message(conv_id, "user", user_msg, db_path=self.db_path)
        
        # Verify title updated to a snippet
        conv = await get_conversation(conv_id, db_path=self.db_path)
        self.assertEqual(conv["title"], "Hello, how do I make sourdough bread...")
        self.assertEqual(len(conv["messages"]), 2)
        self.assertEqual(conv["messages"][1]["role"], "user")
        self.assertEqual(conv["messages"][1]["content"], user_msg)
        
        # Add assistant response
        await add_message(conv_id, "assistant", "To make bread, follow this...", db_path=self.db_path)
        conv = await get_conversation(conv_id, db_path=self.db_path)
        self.assertEqual(len(conv["messages"]), 3)
        self.assertEqual(conv["messages"][2]["role"], "assistant")
        
        # Verify deletion cascades to messages
        await delete_conversation(conv_id, db_path=self.db_path)
        self.assertIsNone(await get_conversation(conv_id, db_path=self.db_path))

    async def test_list_and_sorting(self):
        """Test listing conversations and verify sorting order (most recently updated first)."""
        conv_id1 = await create_conversation(model_name="model1", title="First", db_path=self.db_path)
        conv_id2 = await create_conversation(model_name="model2", title="Second", db_path=self.db_path)
        
        # List and verify they exist
        convs = await list_conversations(db_path=self.db_path)
        self.assertEqual(len(convs), 2)
        
        # Add message to the first one to make it the most recently updated
        await add_message(conv_id1, "user", "bump", db_path=self.db_path)
        
        # Check sorting order
        convs = await list_conversations(db_path=self.db_path)
        self.assertEqual(convs[0]["id"], conv_id1)
        self.assertEqual(convs[1]["id"], conv_id2)

    async def test_update_title(self):
        """Test manually updating the conversation title."""
        conv_id = await create_conversation(model_name="model", title="Old Title", db_path=self.db_path)
        await update_conversation_title(conv_id, "New Title", db_path=self.db_path)
        
        conv = await get_conversation(conv_id, db_path=self.db_path)
        self.assertEqual(conv["title"], "New Title")

if __name__ == "__main__":
    unittest.main()
