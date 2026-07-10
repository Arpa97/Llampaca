import os
import sys
import aiosqlite
import uuid
from pathlib import Path
from contextlib import asynccontextmanager
# pyrefly: ignore [missing-import]
from llampaca.config import DB_PATH

db_path = DB_PATH

@asynccontextmanager
async def get_db_connection(db_path: Path = None):
    """
    Context manager that yields an aiosqlite Connection object.
    Automatically handles commit/rollback on exit and closes the connection.
    Ensures that foreign key constraints are enabled and rows are accessible as Row objects.
    Automatically initializes database tables on the first connection (if file doesn't exist).
    Supports concurrent writes by enabling WAL mode and setting a connection busy timeout.
    """
    path = db_path if db_path is not None else DB_PATH
    
    # Ensure parent directory of the database file exists
    path.parent.mkdir(parents=True, exist_ok=True)
    
    # Check if database file exists and is not empty before connecting
    db_exists = path.exists() and path.stat().st_size > 0
    
    # Connect with a busy timeout of 5 seconds to prevent locking errors under concurrency
    conn = await aiosqlite.connect(str(path), timeout=5.0)
    conn.row_factory = aiosqlite.Row
    
    # SQLite requires foreign keys to be explicitly enabled per connection
    await conn.execute("PRAGMA foreign_keys = ON;")
    # Enable WAL mode to allow non-blocking concurrent reads and writes
    await conn.execute("PRAGMA journal_mode = WAL;")
    
    # Implicit schema creation on first database file initialization
    if not db_exists:
        try:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                );
            """)
            await conn.commit()
        except Exception:
            await conn.rollback()
            await conn.close()
            # Clean up the empty/partially initialized file to prevent corrupted state
            try:
                path.unlink()
            except OSError:
                pass
            raise
            
    try:
        yield conn
        await conn.commit()
    except Exception:
        await conn.rollback()
        raise
    finally:
        await conn.close()

async def init_db(db_path: Path = None):
    """
    Initialize the database by triggering a connection (which automatically creates tables if needed).
    """
    async with get_db_connection(db_path) as _:
        pass

async def create_conversation(model_name: str, title: str = "New Conversation", db_path: Path = None) -> str:
    """
    Create a new conversation entry in the database.
    Returns the generated conversation UUID string.
    """
    conv_id = str(uuid.uuid4())
    
    async with get_db_connection(db_path) as conn:
        await conn.execute(
            "INSERT INTO conversations (id, title, model_name) VALUES (?, ?, ?);",
            (conv_id, title, model_name)
        )
        
    return conv_id

async def add_message(conversation_id: str, role: str, content: str, db_path: Path = None) -> int:
    """
    Add a message to a conversation.
    Updates the 'updated_at' timestamp of the conversation.
    If it's the first user message and the conversation still has the default title,
    it automatically updates the title using a snippet of the message content.
    """
    async with get_db_connection(db_path) as conn:
        # Insert the message
        cursor = await conn.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?);",
            (conversation_id, role, content)
        )
        message_id = cursor.lastrowid
        
        # Update conversation's updated_at timestamp
        await conn.execute(
            "UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
            (conversation_id,)
        )
        
        # Auto-titling logic: if this is the first user message, update default title
        if role == "user":
            cursor = await conn.execute(
                "SELECT COUNT(*) FROM messages WHERE conversation_id = ? AND role = 'user';",
                (conversation_id,)
            )
            row = await cursor.fetchone()
            user_msg_count = row[0]
            
            if user_msg_count == 1:
                cursor = await conn.execute(
                    "SELECT title FROM conversations WHERE id = ?;",
                    (conversation_id,)
                )
                row = await cursor.fetchone()
                if row and row["title"] == "New Conversation":
                    # Generate a nice, clean title from the message snippet
                    snippet = content.strip().replace("\n", " ")
                    if len(snippet) > 40:
                        snippet = snippet[:37].rstrip() + "..."
                    if snippet:
                        await conn.execute(
                            "UPDATE conversations SET title = ? WHERE id = ?;",
                            (snippet, conversation_id)
                        )
                        
    return message_id

async def get_conversation(conversation_id: str, db_path: Path = None) -> dict:
    """
    Retrieve a conversation metadata along with all its messages.
    Returns a dict, or None if the conversation does not exist.
    """
    async with get_db_connection(db_path) as conn:
        # Fetch conversation metadata
        cursor = await conn.execute(
            "SELECT id, title, model_name, created_at, updated_at FROM conversations WHERE id = ?;",
            (conversation_id,)
        )
        conv_row = await cursor.fetchone()
        if not conv_row:
            return None
            
        conv_data = dict(conv_row)
        
        # Fetch conversation messages in ascending order
        cursor = await conn.execute(
            "SELECT role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY id ASC;",
            (conversation_id,)
        )
        rows = await cursor.fetchall()
        messages = [dict(row) for row in rows]
        conv_data["messages"] = messages
        
    return conv_data

async def list_conversations(db_path: Path = None) -> list:
    """
    List all conversations ordered by the last update timestamp (descending).
    """
    async with get_db_connection(db_path) as conn:
        cursor = await conn.execute(
            "SELECT id, title, model_name, created_at, updated_at FROM conversations ORDER BY updated_at DESC;"
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

async def delete_conversation(conversation_id: str, db_path: Path = None):
    """
    Delete a conversation by ID.
    Foreign key CASCADE automatically handles deleting the messages.
    """
    async with get_db_connection(db_path) as conn:
        await conn.execute(
            "DELETE FROM conversations WHERE id = ?;",
            (conversation_id,)
        )

async def update_conversation_title(conversation_id: str, title: str, db_path: Path = None):
    """
    Update the title of a specific conversation.
    """
    async with get_db_connection(db_path) as conn:
        await conn.execute(
            "UPDATE conversations SET title = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
            (title, conversation_id)
        )
