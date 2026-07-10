---
name: verify
description: How to build, launch and drive Llampaca end-to-end to verify changes against the real llama-server and a local model.
---

# Verifying Llampaca

## Prerequisites (already satisfied on this machine)

- Binaries in `~/.llampaca/bin/llama-server` (installed via `llampaca init`)
- A model in `~/.llampaca/models/` (e.g. `Qwen3.5-4B-Q8_0.gguf`)
- Python deps importable via system `python3` (no project .venv exists;
  running `python3 -m llampaca` from the repo root picks up the local package)

## Drive the interactive agent session

`click.prompt` and `click.confirm` both read stdin, so the whole interactive
session can be scripted by piping lines: user messages, `y`/`n` answers to
tool confirmations, and `/exit` to quit cleanly (which shuts the server down).

```bash
# Happy path: trigger a read_file tool call
printf 'Read the file pyproject.toml and summarize it\n/exit\n' | python3 -m llampaca run 2>&1 | tail -30

# Confirmation flow: the 'n' (or 'y') line answers the click.confirm prompt
printf 'Create a file called x.txt with hello\nn\n/exit\n' | python3 -m llampaca run 2>&1 | tail -20

# Plain chat mode
printf 'Say hello\n/exit\n' | python3 -m llampaca run --no-tools 2>&1 | tail -10
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
  paths) can be done via `python3 -c` from the repo root, but always finish
  with a real `llampaca run` session.
