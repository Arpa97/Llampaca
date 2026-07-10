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


def register_filesystem_tools(registry) -> None:
    """Register the filesystem tools on the given ToolRegistry."""
    registry.register(read_file)
    registry.register(list_directory)
    # Writing modifies the user's disk: require explicit confirmation
    registry.register(write_file, requires_confirmation=True)
