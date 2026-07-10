import os
import sys
import sqlite3
import uuid
from pathlib import Path
from contextlib import contextmanager
# pyrefly: ignore [missing-import]
from llampaca.config import DB_PATH

db_path = DB_PATH

@contextmanager
def get_db_connection(db_path: Path = None):
    """
    Context manager that yields a sqlite3 Connection object.
    Automatically handles commit/rollback on exit and closes the connection.
    Ensures that foreign key constraints are enabled and rows are accessible as Row objects.
    Automatically initializes database tables on the first connection (if file doesn't exist).
    """
    path = db_path if db_path is not None else DB_PATH
    
    # Ensure parent directory of the database file exists
    path.parent.mkdir(parents=True, exist_ok=True)
    
    # Check if database file exists and is not empty before connecting
    db_exists = path.exists() and path.stat().st_size > 0
    
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    
    # SQLite requires foreign keys to be explicitly enabled per connection
    conn.execute("PRAGMA foreign_keys = ON;")
    
    # Implicit schema creation on first database file initialization
    if not db_exists:
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                );
            """)
            conn.commit()
        except Exception:
            conn.rollback()
            conn.close()
            # Clean up the empty/partially initialized file to prevent corrupted state
            try:
                path.unlink()
            except OSError:
                pass
            raise
            
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db(db_path: Path = None):
    """
    Initialize the database by triggering a connection (which automatically creates tables if needed).
    """
    with get_db_connection(db_path) as _:
        pass

def create_conversation(model_name: str, title: str = "New Conversation", db_path: Path = None) -> str:
    """
    Create a new conversation entry in the database.
    Returns the generated conversation UUID string.
    """
    conv_id = str(uuid.uuid4())
    
    with get_db_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO conversations (id, title, model_name) VALUES (?, ?, ?);",
            (conv_id, title, model_name)
        )
        
    return conv_id

def add_message(conversation_id: str, role: str, content: str, db_path: Path = None) -> int:
    """
    Add a message to a conversation.
    Updates the 'updated_at' timestamp of the conversation.
    If it's the first user message and the conversation still has the default title,
    it automatically updates the title using a snippet of the message content.
    """
    with get_db_connection(db_path) as conn:
        # Insert the message
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?);",
            (conversation_id, role, content)
        )
        message_id = cursor.lastrowid
        
        # Update conversation's updated_at timestamp
        conn.execute(
            "UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
            (conversation_id,)
        )
        
        # Auto-titling logic: if this is the first user message, update default title
        if role == "user":
            cursor.execute(
                "SELECT COUNT(*) FROM messages WHERE conversation_id = ? AND role = 'user';",
                (conversation_id,)
            )
            user_msg_count = cursor.fetchone()[0]
            
            if user_msg_count == 1:
                cursor.execute(
                    "SELECT title FROM conversations WHERE id = ?;",
                    (conversation_id,)
                )
                row = cursor.fetchone()
                if row and row["title"] == "New Conversation":
                    # Generate a nice, clean title from the message snippet
                    snippet = content.strip().replace("\n", " ")
                    if len(snippet) > 40:
                        snippet = snippet[:37].rstrip() + "..."
                    if snippet:
                        conn.execute(
                            "UPDATE conversations SET title = ? WHERE id = ?;",
                            (snippet, conversation_id)
                        )
                        
    return message_id

def get_conversation(conversation_id: str, db_path: Path = None) -> dict:
    """
    Retrieve a conversation metadata along with all its messages.
    Returns a dict, or None if the conversation does not exist.
    """
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        
        # Fetch conversation metadata
        cursor.execute(
            "SELECT id, title, model_name, created_at, updated_at FROM conversations WHERE id = ?;",
            (conversation_id,)
        )
        conv_row = cursor.fetchone()
        if not conv_row:
            return None
            
        conv_data = dict(conv_row)
        
        # Fetch conversation messages in ascending order
        cursor.execute(
            "SELECT role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY id ASC;",
            (conversation_id,)
        )
        messages = [dict(row) for row in cursor.fetchall()]
        conv_data["messages"] = messages
        
    return conv_data

def list_conversations(db_path: Path = None) -> list:
    """
    List all conversations ordered by the last update timestamp (descending).
    """
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, title, model_name, created_at, updated_at FROM conversations ORDER BY updated_at DESC;"
        )
        return [dict(row) for row in cursor.fetchall()]

def delete_conversation(conversation_id: str, db_path: Path = None):
    """
    Delete a conversation by ID.
    Foreign key CASCADE automatically handles deleting the messages.
    """
    with get_db_connection(db_path) as conn:
        conn.execute(
            "DELETE FROM conversations WHERE id = ?;",
            (conversation_id,)
        )

def update_conversation_title(conversation_id: str, title: str, db_path: Path = None):
    """
    Update the title of a specific conversation.
    """
    with get_db_connection(db_path) as conn:
        conn.execute(
            "UPDATE conversations SET title = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
            (title, conversation_id)
        )
