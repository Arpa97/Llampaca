import os
import sys
import aiosqlite
import uuid
from pathlib import Path
from contextlib import asynccontextmanager
# pyrefly: ignore [missing-import]
from llampaca.config import DB_PATH

db_path = DB_PATH

# Database paths whose schema and migrations have already been verified by
# THIS process. get_db_connection() opens a fresh connection for every
# operation (several per chat turn: user message, assistant message,
# summary update...), and the schema/migration block below — a PRAGMA
# table_info plus several CREATE TABLE/INDEX IF NOT EXISTS statements and a
# commit — is idempotent but not free. The schema cannot change while the
# process runs, so verifying it once per database removes that fixed cost
# from every subsequent connection.
_migrated_paths = set()

@asynccontextmanager
async def get_db_connection(db_path: Path = None):
    """
    Context manager that yields an aiosqlite Connection object.
    Automatically handles commit/rollback on exit and closes the connection.
    Ensures that foreign key constraints are enabled and rows are accessible as Row objects.
    Automatically initializes database tables on the first connection (if file doesn't exist).
    Supports concurrent writes by enabling WAL mode and setting a connection busy timeout.
    Schema creation and migrations run once per database per process (see _migrated_paths).
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

    # Schema/migrations are needed when this process has not verified this
    # database yet, or when the file has disappeared since (e.g. deleted by
    # a test): an empty file must always be (re)initialized.
    needs_init = str(path) not in _migrated_paths or not db_exists

    # Implicit schema creation on first database file initialization
    if not db_exists:
        try:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    summary TEXT DEFAULT NULL,
                    last_summarized_message_id INTEGER DEFAULT NULL,
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
            
    # Run migrations for existing databases to ensure they have the new
    # columns — skipped entirely once this process has verified this
    # database (needs_init above): the schema cannot regress mid-process.
    if needs_init:
        try:
            cursor = await conn.execute("PRAGMA table_info(conversations);")
            columns = [row["name"] for row in await cursor.fetchall()]
            migration_needed = False
            if "summary" not in columns:
                await conn.execute("ALTER TABLE conversations ADD COLUMN summary TEXT DEFAULT NULL;")
                migration_needed = True
            if "last_summarized_message_id" not in columns:
                await conn.execute("ALTER TABLE conversations ADD COLUMN last_summarized_message_id INTEGER DEFAULT NULL;")
                migration_needed = True
            if migration_needed:
                await conn.commit()

            # RAG tables (attachments too large for direct injection). Created
            # unconditionally with IF NOT EXISTS so both fresh and pre-existing
            # databases get them — table creation is idempotent and cheap, so it
            # doubles as its own migration.
            #
            # documents: one row per indexed attachment, owned by a conversation
            # (ON DELETE CASCADE: deleting a chat deletes its index — the reason
            # these tables live in history.db in the first place).
            # embedder_name/embedding_dim record which model produced the
            # vectors: vectors from different embedders are not comparable, so
            # the pipeline checks these before searching and re-indexes on
            # mismatch instead of silently ranking garbage.
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    pages INTEGER,
                    embedder_name TEXT NOT NULL,
                    embedding_dim INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                );
            """)
            # chunks: the retrieval units. "embedding" is the raw float32 BLOB
            # written by rag.serialize_vector; "page" is the page the chunk
            # starts on (NULL for sources without pages); "position" preserves
            # document order for display/debugging.
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id INTEGER NOT NULL,
                    page INTEGER,
                    position INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    embedding BLOB NOT NULL,
                    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
                );
            """)
            # The only query pattern is "all chunks of the documents of one
            # conversation": index both foreign keys.
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id);"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_documents_conversation ON documents(conversation_id);"
            )
            await conn.commit()
            # Only a fully verified schema is remembered: a failure above
            # closes the connection and leaves the path unmarked, so the
            # next connection retries the initialization.
            _migrated_paths.add(str(path))
        except Exception:
            await conn.close()
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

async def add_message(conversation_id: str, role: str, content: str, db_path: Path = None,
                      title_snippet: str = None) -> int:
    """
    Add a message to a conversation.
    Updates the 'updated_at' timestamp of the conversation.
    If it's the first user message and the conversation still has the default title,
    it automatically updates the title using a snippet of the message content.

    Args:
        title_snippet: Optional override for the text the auto-title is cut
            from. Used when `content` is a merged message (attachment blocks
            or index notes + the user's question): titling from `content`
            would name the conversation "[Attached file: ...", so the CLI
            passes the user's own words here instead.
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
                    # (or from the explicit override, see title_snippet).
                    snippet = (title_snippet or content).strip().replace("\n", " ")
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
            "SELECT id, title, model_name, summary, last_summarized_message_id, created_at, updated_at FROM conversations WHERE id = ?;",
            (conversation_id,)
        )
        conv_row = await cursor.fetchone()
        if not conv_row:
            return None
            
        conv_data = dict(conv_row)
        
        # Fetch conversation messages in ascending order
        cursor = await conn.execute(
            "SELECT id, role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY id ASC;",
            (conversation_id,)
        )
        rows = await cursor.fetchall()
        import json
        messages = []
        for row in rows:
            msg = dict(row)
            content = msg["content"]
            if content and isinstance(content, str) and content.startswith("[") and content.endswith("]"):
                try:
                    msg["content"] = json.loads(content)
                except json.JSONDecodeError:
                    pass
            messages.append(msg)
        conv_data["messages"] = messages
        
    return conv_data

async def list_conversations(db_path: Path = None) -> list:
    """
    List all conversations ordered by the last update timestamp (descending).
    """
    async with get_db_connection(db_path) as conn:
        cursor = await conn.execute(
            "SELECT id, title, model_name, summary, last_summarized_message_id, created_at, updated_at FROM conversations ORDER BY updated_at DESC;"
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

async def update_conversation_summary(
    conversation_id: str,
    summary: str,
    last_summarized_message_id: int,
    db_path: Path = None
) -> None:
    """
    Update the conversation summary and the ID of the last summarized message.
    """
    async with get_db_connection(db_path) as conn:
        await conn.execute(
            "UPDATE conversations SET summary = ?, last_summarized_message_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?;",
            (summary, last_summarized_message_id, conversation_id)
        )

# ----------------------------------------------------------------------
# RAG document index (attachments too large for direct injection)
# ----------------------------------------------------------------------

async def add_document(
    conversation_id: str,
    filename: str,
    pages: int,
    embedder_name: str,
    embedding_dim: int,
    db_path: Path = None,
) -> int:
    """
    Register an indexed attachment for a conversation.

    Args:
        pages: Page count of the source (None/0 for pageless sources).
        embedder_name: The embedding model file that produced the vectors.
        embedding_dim: Vector dimension — stored so the pipeline can detect
            an embedder change and re-index instead of comparing
            incompatible vectors.

    Returns:
        The new document's integer id (chunks reference it).
    """
    async with get_db_connection(db_path) as conn:
        cursor = await conn.execute(
            "INSERT INTO documents (conversation_id, filename, pages, embedder_name, embedding_dim)"
            " VALUES (?, ?, ?, ?, ?);",
            (conversation_id, filename, pages, embedder_name, embedding_dim)
        )
        return cursor.lastrowid


async def add_chunks(document_id: int, chunks: list, db_path: Path = None) -> None:
    """
    Store the retrieval chunks of a document in one transaction.

    Args:
        chunks: Dicts with keys "text", "page", "position" (as produced by
            rag.chunk_text) plus "embedding": the raw float32 BLOB from
            rag.serialize_vector. One transaction for all chunks: a
            document must be indexed entirely or not at all — a partial
            index would silently return incomplete search results.
    """
    async with get_db_connection(db_path) as conn:
        await conn.executemany(
            "INSERT INTO chunks (document_id, page, position, text, embedding)"
            " VALUES (?, ?, ?, ?, ?);",
            [
                (document_id, c["page"], c["position"], c["text"], c["embedding"])
                for c in chunks
            ]
        )


async def list_documents(conversation_id: str, db_path: Path = None) -> list:
    """
    The indexed documents of a conversation (metadata only, no chunks),
    oldest first. Used to tell the model what is searchable and to detect
    embedder mismatches on resume.
    """
    async with get_db_connection(db_path) as conn:
        cursor = await conn.execute(
            "SELECT id, filename, pages, embedder_name, embedding_dim, created_at"
            " FROM documents WHERE conversation_id = ? ORDER BY id ASC;",
            (conversation_id,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_conversation_chunks(conversation_id: str, db_path: Path = None) -> list:
    """
    All retrieval chunks of all documents of a conversation, with their
    filename attached — the exact input rag.top_k expects. The whole set is
    loaded in memory by design: ~100 chunks x 4 KB per document, so even
    ten attached documents are a few MB (see rag.py on why brute-force).
    """
    async with get_db_connection(db_path) as conn:
        cursor = await conn.execute(
            "SELECT chunks.id, chunks.document_id, chunks.page, chunks.position,"
            "       chunks.text, chunks.embedding, documents.filename"
            " FROM chunks JOIN documents ON chunks.document_id = documents.id"
            " WHERE documents.conversation_id = ?"
            " ORDER BY chunks.document_id ASC, chunks.position ASC;",
            (conversation_id,)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def delete_document(document_id: int, db_path: Path = None) -> None:
    """
    Remove one indexed document (its chunks go with it via CASCADE).
    Used when re-indexing after an embedder change.
    """
    async with get_db_connection(db_path) as conn:
        await conn.execute(
            "DELETE FROM documents WHERE id = ?;",
            (document_id,)
        )


def get_conversation_chunks_sync(conversation_id: str, db_path: Path = None) -> list:
    """
    Synchronous twin of get_conversation_chunks, for the search_documents
    TOOL: the tool registry executes tools as plain sync functions from
    inside the agent's running event loop, where the aiosqlite variant
    cannot be awaited (and asyncio.run() would raise). A read-only stdlib
    sqlite3 query is safe alongside the async writers thanks to WAL mode.
    Kept here, next to the async version, so the SQL lives in one file.
    """
    import sqlite3

    path = db_path if db_path is not None else DB_PATH
    conn = sqlite3.connect(str(path), timeout=5.0)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT chunks.id, chunks.document_id, chunks.page, chunks.position,"
            "       chunks.text, chunks.embedding, documents.filename"
            " FROM chunks JOIN documents ON chunks.document_id = documents.id"
            " WHERE documents.conversation_id = ?"
            " ORDER BY chunks.document_id ASC, chunks.position ASC;",
            (conversation_id,)
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
