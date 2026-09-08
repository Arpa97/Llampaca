import os
import tempfile
import unittest
from pathlib import Path
import asyncio

from llampaca.config import get_user_documents_dir, resolve_workspace_dir
from llampaca.tools.filesystem import get_workspace_root, set_workspace_root, write_file, read_file
from llampaca.engine.db import init_db, create_conversation, get_conversation, update_conversation_workspace


class TestWorkspaceManagement(unittest.TestCase):

    def test_default_workspace_creation(self):
        """Verify default ~/Documents/LlampacaDocs is created automatically if missing."""
        docs_dir = get_user_documents_dir()
        self.assertTrue(docs_dir.exists())
        self.assertTrue(docs_dir.is_dir())
        self.assertEqual(docs_dir.name, "LlampacaDocs")

    def test_resolve_workspace_custom(self):
        """Verify custom workspace path resolution and directory creation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            custom_path = os.path.join(tmpdir, "MyCustomWorkspace")
            resolved = resolve_workspace_dir(custom_path)
            self.assertTrue(resolved.exists())
            self.assertEqual(resolved, Path(custom_path).resolve())

    def test_context_workspace(self):
        """Verify set_workspace_root and get_workspace_root set per-task context."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ws_path = Path(tmpdir) / "ChatA"
            set_workspace_root(ws_path)
            self.assertEqual(get_workspace_root(), ws_path.resolve())

            # Write file inside context workspace
            res = write_file("test_note.txt", "Hello workspace!")
            self.assertIn("Successfully wrote", res)
            self.assertTrue((ws_path / "test_note.txt").exists())
            self.assertIn("Hello workspace!", read_file("test_note.txt"))

    def test_db_per_chat_workspace(self):
        """Verify per-chat workspace column, migration, and update in DB."""
        async def _async_test():
            with tempfile.TemporaryDirectory() as tmpdir:
                db_file = Path(tmpdir) / "test_history.db"
                await init_db(db_file)

                # Create conversation with workspace_dir
                custom_ws = os.path.join(tmpdir, "ChatWorkspaceB")
                conv_id = await create_conversation("qwen3.5-4b-instruct", title="Test Chat", workspace_dir=custom_ws, db_path=db_file)

                conv = await get_conversation(conv_id, db_path=db_file)
                self.assertIsNotNone(conv)
                self.assertEqual(conv["workspace_dir"], custom_ws)

                # Update workspace
                new_ws = os.path.join(tmpdir, "UpdatedWorkspace")
                await update_conversation_workspace(conv_id, new_ws, db_path=db_file)
                updated_conv = await get_conversation(conv_id, db_path=db_file)
                self.assertEqual(updated_conv["workspace_dir"], new_ws)

        asyncio.run(_async_test())


if __name__ == "__main__":
    unittest.main()
