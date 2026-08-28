"""
Path confinement helpers shared by the tool layer and the HTTP API.

Two independent checks are applied to every path that comes from an
untrusted source (the model, or an HTTP request):

1. **Root confinement** — the resolved path must live inside one of an
   explicit list of allowed roots. Resolution happens with ``Path.resolve()``,
   which also collapses ``..`` segments *and* follows symlinks, so neither a
   traversal string nor a symlink planted inside the workspace can escape.

2. **Sensitive-path deny list** — even inside an allowed root, a small set of
   well-known credential locations is refused. This matters because the user
   is free to point the workspace at their home directory (the GUI folder
   picker allows it, and ``is_project_workspace()`` explicitly contemplates a
   run from ``$HOME``): without this second check, choosing ``$HOME`` as the
   workspace would silently put ``~/.ssh`` back in reach of the model.

Keeping both checks in one module means the filesystem tools and the REST
endpoints cannot drift apart on what "allowed" means.
"""

from pathlib import Path
from typing import Iterable, List, Union

# Paths (relative to the user's home) that are never readable or writable,
# regardless of the workspace in effect. These hold credentials, private keys
# or session tokens: nothing the agent legitimately needs, and exactly what an
# indirect prompt injection would go looking for.
#
# Entries are matched as prefixes: denying ".ssh" denies "~/.ssh/id_rsa" too.
SENSITIVE_HOME_PATHS = (
    # SSH / GPG private keys
    ".ssh",
    ".gnupg",
    # Cloud and container credentials
    ".aws",
    ".kube",
    ".docker/config.json",
    ".azure",
    ".config/gcloud",
    # Developer tokens
    ".netrc",
    ".git-credentials",
    ".npmrc",
    ".pypirc",
    ".config/gh",
    # Llampaca's own configuration: mcp_config.json stores MCP API keys in
    # plaintext, so the agent must not be able to read it back out.
    ".llampaca/mcp_config.json",
    ".llampaca/config.json",
    # OS keychains / credential stores
    "Library/Keychains",
    "AppData/Local/Microsoft/Credentials",
    "AppData/Roaming/Microsoft/Crypto",
    # Browser profiles (cookies, saved passwords)
    "Library/Application Support/Google/Chrome",
    "Library/Application Support/Firefox",
    ".mozilla",
    ".config/google-chrome",
    ".config/chromium",
)


class PathNotAllowed(PermissionError):
    """Raised when a path fails confinement. Subclasses PermissionError so
    existing ``except PermissionError`` handlers keep working."""


def is_sensitive_path(resolved: Path) -> bool:
    """
    True if `resolved` (an already-resolved absolute path) is inside one of
    the SENSITIVE_HOME_PATHS entries.

    Compares resolved-against-resolved so a symlink pointing at ~/.ssh is
    caught as well as a literal path.
    """
    try:
        home = Path.home().resolve()
    except (OSError, RuntimeError):
        return False

    for relative in SENSITIVE_HOME_PATHS:
        candidate = home / relative
        try:
            # strict=False: most of these paths do not exist on any given
            # machine, and a non-existent path still needs to be denied.
            denied = candidate.resolve()
        except OSError:
            continue
        if resolved == denied or resolved.is_relative_to(denied):
            return True
    return False


def confine_path(
    path: Union[str, Path],
    roots: Iterable[Path],
    *,
    allow_sensitive: bool = False,
) -> Path:
    """
    Resolve `path` and require it to sit inside one of `roots`.

    Args:
        path: The untrusted path (may be relative, may contain '..' or '~').
        roots: Allowed base directories. The first match wins.
        allow_sensitive: Skip the credential deny list. Only ever set this
            for paths the *user* typed directly in a native OS dialog, never
            for paths chosen by the model or received over HTTP.

    Returns:
        The resolved, allowed path.

    Raises:
        PathNotAllowed: if the path escapes every root, or hits the deny list.
    """
    resolved = Path(path).expanduser().resolve()

    if not allow_sensitive and is_sensitive_path(resolved):
        raise PathNotAllowed(
            f"Access to '{path}' is denied: it points at a location holding "
            "credentials or private keys."
        )

    resolved_roots: List[Path] = []
    for root in roots:
        try:
            resolved_roots.append(Path(root).expanduser().resolve())
        except OSError:
            continue  # a root that cannot be resolved simply grants nothing

    for root in resolved_roots:
        if resolved == root or resolved.is_relative_to(root):
            return resolved

    allowed = ", ".join(str(r) for r in resolved_roots) or "(none)"
    raise PathNotAllowed(
        f"Path '{path}' is outside the allowed directories ({allowed})."
    )


def media_roots() -> List[Path]:
    """
    Directories the HTTP API may serve files from (`/api/media`) and hand to
    the OS opener (`/api/open-file`).

    Generated images land in ``~/Documents/LlampacaDocs/Images``, and the user
    may also ask the agent to write a file into the active workspace and then
    click it in the chat — so both roots are needed. Note that
    ``~/.llampaca`` is deliberately *not* here: it holds mcp_config.json.
    """
    # Imported lazily: llampaca.tools.filesystem imports this module, so a
    # module-level import would be circular.
    from llampaca.config import get_user_documents_dir
    from llampaca.tools.filesystem import get_workspace_root

    roots = [get_user_documents_dir()]
    try:
        roots.append(get_workspace_root())
    except Exception:
        pass  # no active workspace: the documents dir alone is enough
    return roots
