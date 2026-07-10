# Changelog

All notable changes to the Llampaca project will be documented in this file.
This project adheres to Semantic Versioning and complies with development logging guidelines.

## [2026-07-10]

### Added
- **Database Storage (`llampaca/engine/db.py`)**: Integrated SQLite-based persistent storage for conversation history. Added tables `conversations` (UUID identifier, title, model, timestamps) and `messages` (role, content, creation timestamp) with `ON DELETE CASCADE` constraints.
- **CRUD Operations**: Implemented connection managers and functions to create, query, list, update, and delete conversations and messages.
- **Auto-Titling Logic**: Automatically sets the conversation title using a truncated snippet of the first user message when a conversation is initialized with the default title.
- **Unit Tests (`tests/test_db.py`)**: Added test coverage verifying the schema integrity, auto-titling thresholds, message cascade deletions, and timestamp-based sorting.

### Modified
- **Configuration (`llampaca/config.py`)**: Added `DB_PATH` parameter pointing to `~/.llampaca/history.db` to keep all persistent application files grouped.
- **Documentation (`README.md`)**: Updated the system layout table and added a dedicated database & schemas specification section documenting `conversations` and `messages` tables.
- **Server Database Integration (`llampaca/engine/server.py` & `db.py`)**: Defined `self.db` in `LlamaServer` and added `db_path` alias in `db.py` to support user database verification output on server start.
- **CLI Commands (`llampaca/cli.py`)**: Updated `history list` command to call `list_conversations()` instead of the undefined `get_history()`.
- **Implicit Database Initialization (`llampaca/engine/db.py`)**: Refactored database initialization to happen implicitly inside `get_db_connection()` on the first query (only if the file is missing or empty), removing redundant/multiple `init_db()` calls from the CLI startup sequence.
