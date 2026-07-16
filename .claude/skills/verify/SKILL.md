---
name: verify
description: How to build, launch and drive Llampaca end-to-end to verify changes against the real llama-server and a local model.
---

# Verifying Llampaca

## The two Python environments (IMPORTANT)

The user's real `llampaca` command runs from a **conda env**:
`/Users/umbertodilaudo/miniconda3/envs/llampaca` (Python 3.11, editable
install pointing at this repo). System `python3` also has the deps and can
run `python3 -m llampaca` from the repo root.

Because both are editable/repo-based, **code changes are picked up by both
automatically** — but **dependencies diverge**. This has already caused a
real incident: a new dep installed only into system python3 made the
feature pass verification while crashing the user's real sessions.

Rules:
- **New dependency? Install it in BOTH environments:**
  `/Users/umbertodilaudo/miniconda3/envs/llampaca/bin/python -m pip install <pkg>`
  and `python3 -m pip install <pkg>`.
- **End-to-end verification: prefer the conda binary**
  (`/Users/umbertodilaudo/miniconda3/envs/llampaca/bin/llampaca`) — it is
  what the user actually runs.
- The conda env has no pytest: run tests there with
  `.../envs/llampaca/bin/python -m unittest discover tests`.

## Prerequisites (already satisfied on this machine)

- Binaries in `~/.llampaca/bin/llama-server` (installed via `llampaca init`)
- A model in `~/.llampaca/models/` (e.g. `Qwen3.5-4B-Q8_0.gguf`)

## Drive the interactive agent session

`click.prompt` and `click.confirm` both read stdin, so the whole interactive
session can be scripted by piping lines: user messages, `y`/`n` answers to
tool confirmations, and `/exit` to quit cleanly (which shuts the server down).

If conversation history exists (`llampaca history list` to check), a
session-selection menu appears first — the initial piped line must then be
`1` (start a new conversation).

```bash
LLAMPACA=/Users/umbertodilaudo/miniconda3/envs/llampaca/bin/llampaca

# Happy path: trigger a read_file tool call ("1" answers the session menu)
printf '1\nRead the file pyproject.toml and summarize it\n/exit\n' | $LLAMPACA run 2>&1 | tail -30

# Confirmation flow: the 'n' (or 'y') line answers the click.confirm prompt
printf '1\nCreate a file called x.txt with hello\nn\n/exit\n' | $LLAMPACA run 2>&1 | tail -20

# Plain chat mode
printf '1\nSay hello\n/exit\n' | $LLAMPACA run --no-tools 2>&1 | tail -10

# Attachment flow (/attach stages a file; it is sent with the next message)
printf '1\n/attach README.md\nWhat is the attached file about?\n/exit\n' | $LLAMPACA run 2>&1 | tail -15
```

Model load takes ~10s; give commands a generous timeout (300s is safe).

## What to look for

- `[tool] name({...})` cyan lines = the model requested a tool
- `[result] ...` green lines = tool executed, one-line preview
- Confirmation prompts appear for `write_file` and `run_shell_command`
- Server logs: `~/.llampaca/logs/llama-server-<port>.log`

## Gotchas

- The server binds port 8080 (auto-increments if busy) and kills orphaned
  llama-server processes at startup — don't run two sessions in parallel
  expecting isolation.
- Unit-level checks of the tool registry (schema generation, sandbox, error
  paths) can be done via `python3 -c` (or the conda env's python) from the
  repo root, but always finish with a real `llampaca run` session using the
  conda binary.
