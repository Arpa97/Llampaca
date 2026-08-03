"""
Built-in filesystem tools: read, write and list files.

Safety model: all paths are sandboxed to the *workspace root*, which is the
directory where the user launched `llampaca run`. Every path coming from the
model is resolved and checked to be inside that root, so the model cannot
read or write outside of it (e.g. `../../etc/passwd` is rejected).

Writing files additionally requires interactive user confirmation (flagged
via requires_confirmation on registration — the agent loop handles the
actual prompt, keeping these functions UI-independent).
"""

import contextvars
from pathlib import Path
from typing import Union
from llampaca.config import resolve_workspace_dir

# Maximum number of characters returned when reading a file. Larger files
# are truncated by the registry anyway, but we cut early here to avoid
# loading a multi-GB file into memory by mistake.
MAX_READ_CHARS = 20000

# Limits for the search tools, to keep results within the small context
# window of local models and to keep scans fast.
MAX_SEARCH_MATCHES = 50          # max matching lines returned by search_text
MAX_FOUND_FILES = 100            # max paths returned by find_files
MAX_SEARCHABLE_FILE_BYTES = 2_000_000  # skip files bigger than ~2 MB when grepping

# Directories that are never worth searching: VCS internals, caches,
# dependency trees. Skipping them keeps scans fast and results relevant.
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv",
             ".cache", ".idea", ".vscode", "dist", "build", ".eggs"}

# Context-aware workspace storage for thread-safe/task-safe per-chat workspaces
_active_workspace_var: contextvars.ContextVar[Path] = contextvars.ContextVar(
    "active_workspace", default=resolve_workspace_dir()
)


def get_workspace_root() -> Path:
    """Returns the active workspace directory for the current turn/chat, creating it if needed."""
    ws = _active_workspace_var.get()
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def set_workspace_root(path: Union[str, Path, None]) -> Path:
    """Set the active workspace directory for the current turn/chat context."""
    resolved = resolve_workspace_dir(str(path) if path else None)
    _active_workspace_var.set(resolved)
    return resolved


def _resolve_in_workspace(path: str) -> Path:
    """
    Resolve a model-provided path safely inside the workspace root or user home.

    Relative paths are resolved against the workspace root. Tilde (~) and
    absolute paths pointing inside the user home directory are expanded and allowed.
    """
    ws = get_workspace_root()
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = ws / candidate
    resolved = candidate.resolve()

    home = Path.home().resolve()
    if not (resolved.is_relative_to(ws) or resolved.is_relative_to(home)):
        raise PermissionError(
            f"Path '{path}' is outside the workspace ({ws}) and home directory ({home}). "
            "Only paths inside the workspace or home directory are allowed."
        )
    return resolved


def read_file(path: str, start_line: int = 0, max_lines: int = 0) -> str:
    """
    Read a text file from the workspace and return its content. To read only
    part of a large file, pass start_line (and optionally max_lines): use this
    to jump straight to a line number reported by search_text, or to continue
    reading a file that was truncated, instead of re-reading it from the start.

    Args:
        path: Path of the file to read, relative to the workspace directory.
        start_line: First line to read, 1-indexed. Omit or use 0 to read from
            the beginning of the file.
        max_lines: How many lines to read starting at start_line. Omit or use
            0 to read to the end of the file (still capped in size).
    """
    full_path = _resolve_in_workspace(path)
    if not full_path.is_file():
        raise FileNotFoundError(f"File '{path}' does not exist.")

    # Read UTF-8, replacing non-decodable bytes so binary garbage degrades
    # gracefully to replacement characters instead of raising UnicodeDecodeError.
    with open(full_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read(MAX_READ_CHARS + 1)

    lines = content.splitlines()
    total_lines = len(lines)

    # 1-indexed start_line: 0 or 1 both start at line 1
    first_idx = max(0, start_line - 1) if start_line > 0 else 0
    if first_idx >= total_lines:
        return f"[File '{path}' has {total_lines} lines; start_line={start_line} is past the end.]"

    if max_lines > 0:
        last_idx = min(total_lines, first_idx + max_lines)
    else:
        last_idx = total_lines

    selected = "\n".join(lines[first_idx:last_idx])
    header = f"=== File: {path} (lines {first_idx + 1}-{last_idx} of {total_lines}) ==="

    footer = ""
    if len(content) > MAX_READ_CHARS:
        footer += f"\n[File '{path}' is very large; content truncated at {MAX_READ_CHARS} characters.]"
    elif last_idx < total_lines:
        footer += (
            f"\n[Showing lines {first_idx + 1}-{last_idx} of {total_lines}. "
            f"To continue, call read_file with start_line={last_idx + 1}.]"
        )
    return f"{header}\n{selected}{footer}"


def is_project_workspace() -> bool:
    """Returns True if the current workspace looks like a specific project directory,
       False if it's a global run (like Home dir or an App bundle)."""
    ws = get_workspace_root()
    home = Path.home().resolve()
    if ws == home or ".app/Contents" in str(ws) or str(ws) == "/":
        return False
    return True


def write_file(path: str, content: str) -> str:
    """
    Write text content to a file. Writes to the active workspace directory.
    Overwrites the file if it already exists.

    Args:
        path: Path of the file to write (relative to workspace or absolute).
        content: The full text content to write into the file.
    """
    resolved = _resolve_in_workspace(path)

    if resolved.is_dir():
        return f"Error: '{path}' is an existing directory, cannot write a file there."

    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content, encoding="utf-8")
    return f"Successfully wrote {len(content)} characters to '{path}'."


def edit_file(path: str, old_text: str, new_text: str) -> str:
    """
    Edit a text file by replacing an exact snippet of its content with new
    text, leaving the rest of the file untouched. Prefer this over write_file
    for changing existing files: you don't need to rewrite the whole file.

    Args:
        path: Path of the file to edit, relative to the workspace directory.
        old_text: The exact text to find in the file. It must appear exactly
            once — include enough surrounding lines to make it unique.
        new_text: The text to replace it with.
    """
    resolved = _resolve_in_workspace(path)
    if not resolved.exists():
        return f"Error: file '{path}' does not exist."
    if resolved.is_dir():
        return f"Error: '{path}' is a directory, not a file."

    content = resolved.read_text(encoding="utf-8", errors="replace")

    # Require a unique match: replacing an ambiguous snippet could silently
    # change the wrong place, which is worse than failing loudly.
    occurrences = content.count(old_text)
    if occurrences == 0:
        return (
            f"Error: the text to replace was not found in '{path}'. "
            "Check the exact content with read_file first (whitespace matters)."
        )
    if occurrences > 1:
        return (
            f"Error: the text to replace appears {occurrences} times in '{path}'. "
            "Include more surrounding context in old_text to make it unique."
        )

    resolved.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
    return f"Successfully edited '{path}' (1 replacement)."


def delete_path(path: str) -> str:
    """
    Permanently delete a file or an entire directory (including all its
    contents) from the workspace. This cannot be undone, so use it only when
    the user explicitly asks to remove something. The user is always asked
    for confirmation before anything is deleted.

    Args:
        path: Path of the file or directory to delete, relative to the
            workspace directory.
    """
    import shutil

    resolved = _resolve_in_workspace(path)

    # Extra guards on top of the sandbox: deleting these is catastrophic and
    # never what the user meant by removing "a file or folder".
    if resolved == WORKSPACE_ROOT:
        return "Error: refusing to delete the workspace root directory itself."
    relative_parts = resolved.relative_to(WORKSPACE_ROOT).parts
    if relative_parts and relative_parts[0] == ".git":
        return (
            "Error: refusing to delete '.git' (or anything inside it) — that "
            "would destroy the project's version history."
        )

    if not resolved.exists():
        return f"Error: '{path}' does not exist."

    if resolved.is_dir():
        # Count what is about to disappear so the result is informative
        contained = sum(1 for _ in resolved.rglob("*"))
        shutil.rmtree(resolved)
        return f"Deleted directory '{path}' and its {contained} contained item(s)."

    resolved.unlink()
    return f"Deleted file '{path}'."


def list_directory(path: str = ".") -> str:
    """
    List files and subdirectories at a path inside the workspace.

    Args:
        path: Directory to list, relative to the workspace. Defaults to the
            workspace root itself.
    """
    resolved = _resolve_in_workspace(path)
    if not resolved.exists():
        return f"Error: directory '{path}' does not exist."
    if not resolved.is_dir():
        return f"Error: '{path}' is a file, not a directory."

    entries = []
    for entry in sorted(resolved.iterdir()):
        # Mark directories with a trailing slash, show file sizes for files
        if entry.is_dir():
            entries.append(f"{entry.name}/")
        else:
            entries.append(f"{entry.name} ({entry.stat().st_size} bytes)")

    if not entries:
        return f"Directory '{path}' is empty."
    return "\n".join(entries)


def _iter_searchable_files(start: Path):
    """
    Walk the tree under `start`, yielding files worth searching: skips
    VCS/cache/dependency directories and hidden entries.
    """
    if start.is_file():
        yield start
        return
    # Manual stack-based walk so we can prune SKIP_DIRS before descending
    stack = [start]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except OSError:
            continue  # unreadable directory: skip it
        for entry in entries:
            name = entry.name
            if name.startswith(".") or name in SKIP_DIRS:
                continue
            if entry.is_dir():
                stack.append(entry)
            elif entry.is_file():
                yield entry


def find_files(pattern: str) -> str:
    """
    Find files in the workspace whose name matches a glob pattern, searching
    all subdirectories. Use this to locate a file when you don't know where
    it is.

    Args:
        pattern: Glob pattern matched against file names, e.g. '*.py',
            'config.*' or 'README.md'.
    """
    import fnmatch

    matches = []
    for file in _iter_searchable_files(WORKSPACE_ROOT):
        if fnmatch.fnmatch(file.name, pattern):
            matches.append(str(file.relative_to(WORKSPACE_ROOT)))
            if len(matches) >= MAX_FOUND_FILES:
                break

    if not matches:
        return f"No files matching '{pattern}' found in the workspace."
    header = f"Files matching '{pattern}':"
    if len(matches) >= MAX_FOUND_FILES:
        header += f" (showing first {MAX_FOUND_FILES})"
    return header + "\n" + "\n".join(matches)


def search_text(text: str, path: str = ".") -> str:
    """
    Search for a text string inside the files of the workspace (like grep,
    case-insensitive). Returns matching lines as 'file:line_number: content'.
    Use this to find where something is defined or mentioned in the code.

    Args:
        text: The text to search for (plain substring, not a regex).
        path: Directory or file to search in, relative to the workspace.
            Defaults to the whole workspace.
    """
    start = _resolve_in_workspace(path)
    if not start.exists():
        return f"Error: path '{path}' does not exist."

    needle = text.lower()
    matches = []
    truncated = False

    for file in _iter_searchable_files(start):
        try:
            if file.stat().st_size > MAX_SEARCHABLE_FILE_BYTES:
                continue  # too big: almost certainly not source/text we want
            raw = file.read_bytes()
        except OSError:
            continue
        if b"\x00" in raw[:1024]:
            continue  # binary file: skip

        content = raw.decode("utf-8", errors="replace")
        rel = file.relative_to(WORKSPACE_ROOT)
        for line_number, line in enumerate(content.splitlines(), start=1):
            if needle in line.lower():
                # Trim very long lines so one minified file can't flood the result
                shown = line.strip()
                if len(shown) > 200:
                    shown = shown[:200] + "..."
                matches.append(f"{rel}:{line_number}: {shown}")
                if len(matches) >= MAX_SEARCH_MATCHES:
                    truncated = True
                    break
        if truncated:
            break

    if not matches:
        return f"No matches for '{text}' found in '{path}'."
    header = f"Matches for '{text}':"
    if truncated:
        header += f" (stopped at {MAX_SEARCH_MATCHES} matches — refine the search to see more)"
    return header + "\n" + "\n".join(matches)


def register_filesystem_tools(registry) -> None:
    """Register the filesystem tools on the given ToolRegistry."""
    registry.register(read_file)
    registry.register(list_directory)
    registry.register(find_files)
    registry.register(search_text)
    # Writing/editing/deleting modifies the user's disk: require explicit
    # confirmation. Deletion is the most dangerous of all (irreversible).
    registry.register(write_file, requires_confirmation=True)
    registry.register(edit_file, requires_confirmation=True)
    registry.register(delete_path, requires_confirmation=True)
