# AGENTS.md — lfsrclone

## Project Overview

**lfsrclone** is an rclone-based custom transfer agent for git-lfs. It implements the
[git-lfs custom transfer protocol](https://github.com/git-lfs/git-lfs/blob/main/docs/custom-transfers.md),
reading JSON messages from stdin and writing JSON responses to stdout, while delegating
actual file transfer to rclone via subprocess.

Single-file Python module (`lfsrclone.py`, ~230 lines) with no third-party runtime
dependencies — stdlib only. Status: BETA, not actively developed.

## Architecture

```
git-lfs  <--JSON over stdin/stdout-->  lfsrclone.py  <--subprocess-->  rclone
```

- `main()`: Entry point. Constructs `Main()` and calls `.run()`.
- `Main.__init__()`: CLI parsing (argparse) and logging setup only — no I/O.
- `Main.run()`: Starts the LFS protocol: calls `init()` then `loop()`.
- `Main.init()`: Handles the LFS `init` event (handshake).
- `Main.loop()`: Event loop — dispatches `upload`/`download`/`terminate` events.
- `Main.action()`: Builds rclone command, runs it, streams progress, reports completion.
- `pathjoin()`: rclone-aware path join (handles `:` in remote paths).
- `read()` / `write()`: JSON protocol over stdin/stdout.

Files stored on remote as `<oid[:2]>/<oid[2:4]>/<oid>` (content-addressable by SHA256).

## Build & Run Commands

### Install (development)

```bash
# Create venv and install in editable mode
uv venv --python 3.11
source .venv/bin/activate
uv pip install -e .

# Or run directly without installing
python lfsrclone.py <remote> [options]
```

### Run Tests

Tests require **git**, **git-lfs**, and **rclone** on PATH. Install via homebrew:

```bash
brew install git-lfs rclone
git lfs install   # one-time global setup
```

```bash
# Run all tests
pytest tests/ -v

# Run a single test by name
pytest tests/ -v -k test_lfs_pull
pytest tests/ -v -k test_prune

# Run with output visible (useful for debugging git/rclone calls)
pytest tests/ -v -s
```

Tests are integration tests — they run real git, git-lfs, and rclone against a
local rclone alias remote. A session-scoped fixture builds the full environment
once; tests are independent and can be run individually.

### Formatting

```bash
black lfsrclone.py tests/

# Check without modifying
black --check lfsrclone.py tests/
```

### Linting

```bash
ruff check lfsrclone.py tests/

# Auto-fix what ruff can
ruff check --fix lfsrclone.py tests/
```

Both tools are configured in `pyproject.toml`. Run both before committing.

## Code Style Guidelines

### Formatter

**black** — all code must pass `black --check`. This is the only enforced style rule.

### Linter

**ruff** — configured in `pyproject.toml`. Rules enabled: E, W, F (pyflakes), I (isort),
B (bugbear), UP (pyupgrade). E501 (line length) is disabled — black handles that.

### Python Version

Minimum **Python 3.9** (`requires-python = ">=3.9"` in `pyproject.toml`). The codebase
uses f-strings and `encoding=` kwarg to `logging.basicConfig` (requires 3.9+).

### Imports

Ruff enforces isort ordering. Each import on its own line:

```python
import argparse
import json
import logging
import os
```

stdlib only — no third-party runtime imports.

### Naming Conventions

- **Classes**: `PascalCase` (`Main`)
- **Functions**: `snake_case` (`pathjoin`, `main`)
- **Variables**: `snake_case` (`rclone_args`, `repopath`)
- **Constants**: `UPPER_SNAKE_CASE` if adding any
- **Module version**: `__version__` in `lfsrclone.py`

### Type Annotations

Not currently used — listed as a TODO in the roadmap. Do not add type annotations
to existing code unless specifically asked. New standalone functions may include
annotations at your discretion.

### Error Handling

- **Critical protocol errors**: `logging.critical()` then `sys.exit(1)`
- **Subprocess errors**: Collected from rclone's JSON stderr, reported via the
  git-lfs error protocol (`complete` event with `error` field)
- **JSON decode errors**: Caught with `try/except json.JSONDecodeError`, appended
  to error list, logged — does not crash the process
- **No bare `except:`** — always catch specific exceptions
- **No custom exception classes** — uses stdlib exceptions and sys.exit

### Logging

```python
logging.basicConfig(
    filename=args.log_file,      # Default: .git/lfsrclone.log
    encoding="utf-8",
    format="%(levelname)s:%(asctime)s: %(message)s",
    level=getattr(logging, args.log_level),
)
```

Use `logging.debug()` for tracing, `logging.info()` for milestones, `logging.critical()`
for fatal errors. Never use `print()` for diagnostics — stdout is reserved for the
git-lfs JSON protocol.

### stdout Is Sacred

`print()` / `sys.stdout` is the git-lfs communication channel. All diagnostic output
must go through `logging` (which writes to a file). Writing anything unexpected to
stdout will break the protocol and corrupt git-lfs operations.

### JSON Protocol

Messages are single-line JSON objects on stdin/stdout. The `write()` and `read()`
functions handle serialization. Event types: `init`, `upload`, `download`, `terminate`,
`progress`, `complete`.

### Subprocess Usage

rclone is invoked via `subprocess.Popen` with `stdout=PIPE, stderr=PIPE`. Progress
is parsed from rclone's JSON log output on stderr. Always use `--use-json-log` and
`--ask-password=false` when calling rclone.

## Testing Notes

- `tests/conftest.py` provides session-scoped fixtures: `lfs_env` (bare host repo,
  configured repo1 with two commits pushed), `skip_smudge_clone` (repo2, not pulled),
  `pulled_clone` (repo3, pulled).
- Tests are independent — each takes the fixtures it needs; fixtures run once per session.
- Tests use `subprocess.check_call` — any nonzero exit code fails the test immediately.
- Assertions verify SHA256 hashes match between local files and remote storage.
- `tmp_path_factory` manages temp dirs; pytest cleans up on success.

## Key Constraints for Agents

1. **Do not write to stdout** except through the `write()` function for protocol messages.
2. **No third-party dependencies** — stdlib only by design.
3. **Run `black` and `ruff`** on any modified Python files before committing.
4. **Single-file module** — all production code lives in `lfsrclone.py`.
5. **The `pathjoin()` function is not `os.path.join`** — it handles rclone remote
   colon syntax; do not replace it with `os.path.join` or `pathlib`.
6. **`Main.__init__` must stay side-effect-free** — no I/O in the constructor.
   Call `Main().run()` to start the agent (this is what `main()` does).
7. **Bugfix rule**: fix minimally. Do not refactor unrelated code while fixing bugs.
