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

from pathlib import Path

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

# The sandbox root: fixed at import time to the current working directory,
# i.e. where the user started llampaca.
WORKSPACE_ROOT = Path.cwd().resolve()


def _resolve_in_workspace(path: str) -> Path:
    """
    Resolve a model-provided path safely inside the workspace root.

    Relative paths are resolved against the workspace root. Absolute paths
    are allowed only if they already point inside it. Raises PermissionError
    for anything that escapes the sandbox.
    """
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = WORKSPACE_ROOT / candidate
    resolved = candidate.resolve()

    if not resolved.is_relative_to(WORKSPACE_ROOT):
        raise PermissionError(
            f"Path '{path}' is outside the workspace ({WORKSPACE_ROOT}). "
            "Only paths inside the workspace are allowed."
        )
    return resolved


def read_file(path: str) -> str:
    """
    Read a text file from the workspace and return its content.

    Args:
        path: Path of the file to read, relative to the workspace directory.
    """
    resolved = _resolve_in_workspace(path)
    if not resolved.exists():
        return f"Error: file '{path}' does not exist."
    if resolved.is_dir():
        return f"Error: '{path}' is a directory. Use list_directory to inspect it."

    # errors="replace" so binary junk doesn't raise, it just shows up mangled
    content = resolved.read_text(encoding="utf-8", errors="replace")
    if len(content) > MAX_READ_CHARS:
        content = content[:MAX_READ_CHARS] + f"\n... [truncated: file is {len(content)} characters]"
    return content


def write_file(path: str, content: str) -> str:
    """
    Write text content to a file in the workspace, creating parent directories
    if needed. Overwrites the file if it already exists.

    Args:
        path: Path of the file to write, relative to the workspace directory.
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
    # Writing/editing modifies the user's disk: require explicit confirmation
    registry.register(write_file, requires_confirmation=True)
    registry.register(edit_file, requires_confirmation=True)
