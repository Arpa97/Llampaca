"""
Built-in shell tool: run a shell command on the user's machine.

This is the most powerful and most dangerous tool, so it is always
registered with requires_confirmation=True — the agent loop shows the exact
command to the user and only executes it if they approve. The confirmation
prompt itself lives in the UI layer (CLI), not here, so this module stays
UI-independent.
"""

import subprocess

# Commands are killed after this many seconds so a hanging command
# (e.g. an interactive program waiting for input) cannot freeze the agent.
COMMAND_TIMEOUT_SECONDS = 60


def run_shell_command(command: str) -> str:
    """
    Execute a shell command on the user's machine and return its output.
    The user is asked for confirmation before the command runs.

    Args:
        command: The shell command to execute (e.g. 'ls -la', 'git status').
    """
    import os
    from llampaca.config import LLAMPACA_DIR

    env = os.environ.copy()
    node_modules_path = str(LLAMPACA_DIR / "node_modules")
    existing_node_path = env.get("NODE_PATH", "")
    env["NODE_PATH"] = f"{node_modules_path}:{existing_node_path}".strip(":") if existing_node_path else node_modules_path

    try:
        completed = subprocess.run(
            command,
            shell=True,  # the model produces a full shell line, not an argv list
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {COMMAND_TIMEOUT_SECONDS} seconds."

    # Give the model everything it needs to judge the outcome:
    # exit code, stdout and stderr.
    parts = [f"Exit code: {completed.returncode}"]
    if completed.stdout:
        parts.append(f"stdout:\n{completed.stdout}")
    if completed.stderr:
        parts.append(f"stderr:\n{completed.stderr}")
    if not completed.stdout and not completed.stderr:
        parts.append("(no output)")
    return "\n".join(parts)


def register_shell_tools(registry) -> None:
    """Register the shell tool on the given ToolRegistry."""
    # Arbitrary command execution: always gated behind user confirmation
    registry.register(run_shell_command, requires_confirmation=True)
