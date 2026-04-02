# lfsrclone

An [rclone][rclone]-backed [custom transfer agent][cta] for [git-lfs][lfs]. Instead of spawning a new rclone process per file, lfsrclone starts a single persistent `rclone rcd` daemon at the beginning of each session and issues transfers over its HTTP RC API. This keeps startup overhead low and works with any remote that rclone supports.

> **Beta.** Works well in practice but has not been exhaustively tested.

[rclone]: https://rclone.org
[cta]: https://github.com/git-lfs/git-lfs/blob/main/docs/custom-transfers.md
[lfs]: https://git-lfs.github.com/

## Requirements

- Python 3.9+
- [git-lfs](https://git-lfs.com) on `PATH`
- [rclone](https://rclone.org) on `PATH` and configured with at least one remote

## Installation

Install as a system tool with [uv](https://docs.astral.sh/uv/):

```bash
uv tool install git+https://github.com/NotAFood/lfsrclone
```

This places the `lfsrclone` command on your `PATH`.

## Configuration

lfsrclone is a standalone transfer agent — it does not need a special LFS server. LFS metadata (`.gitattributes`, pointer files) is stored in the git repo as normal; only file content lives on the rclone remote.

### New repo

Make sure git-lfs is initialized globally and you have tracked at least one file pattern:

```bash
git lfs install           # one-time global setup
git lfs track "*.psd"     # track a file type
git add .gitattributes
```

Then configure the transfer agent for the repo:

```bash
git config lfs.customtransfer.lfsrclone.path lfsrclone
git config lfs.standalonetransferagent lfsrclone
git config lfs.customtransfer.lfsrclone.args "myremote:path/to/lfs-storage"
```

Replace `myremote:path/to/lfs-storage` with your rclone remote and path. Any lfsrclone options (see [Options](#options)) can be appended to that string. Unrecognized arguments are passed directly to `rclone rcd`.

**Tip:** if the args string is complex, set a placeholder and edit `.git/config` directly:

```bash
git config lfs.customtransfer.lfsrclone.args TMP
```

```ini
[lfs "customtransfer.lfsrclone"]
    path = lfsrclone
    args = myremote:path/to/lfs-storage \
           --log-level DEBUG \
           --bwlimit 10M
```

### Cloning an existing repo

Cloning presents a chicken-and-egg problem: lfsrclone must be configured before git-lfs can pull files, but the config lives in the repo you are cloning. Work around it by skipping the smudge filter during clone:

```bash
GIT_LFS_SKIP_SMUDGE=1 git clone <repo>
cd <repo>

git config lfs.customtransfer.lfsrclone.path lfsrclone
git config lfs.standalonetransferagent lfsrclone
git config lfs.customtransfer.lfsrclone.args "myremote:path/to/lfs-storage"

git lfs pull
```

### File locking

lfsrclone does not support git-lfs file locking. If your workflow requires pushing to a remote that enforces lock verification, disable it:

```bash
git config lfs.locksverify false
```

## Options

These flags go in `lfs.customtransfer.lfsrclone.args`, after the remote:

| Flag | Default | Description |
|------|---------|-------------|
| `--log-file PATH` | `.git/lfsrclone.log` | Log file path |
| `--log-level LEVEL` | `WARNING` | Log verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`, or `NONE` |
| `--rclone-exe PATH` | `rclone` | Path to the rclone executable |
| `--temp-dir PATH` | `.git/lfsrclone-tmp` | Temporary directory for downloads |
| `--rc-stats-interval SEC` | `0.1` | Progress polling interval in seconds |

## Concurrency

The [custom transfer protocol][cta] is serial per process: git-lfs sends one transfer event and waits for the `complete` response before sending the next. Parallelism is achieved by git-lfs spawning **multiple lfsrclone processes simultaneously**, each handling its own serial stream of transfers.

The right knob is [`lfs.concurrenttransfers`][concurrent] (default 8), which controls how many lfsrclone processes git-lfs runs at once. Each process starts one `rclone rcd` daemon at init time and reuses it for all its transfers, so startup cost is paid once per session rather than once per file.

rclone's `--transfers` flag has no effect here: each daemon handles one file at a time by protocol, and splitting transfers across multiple daemons is already what `lfs.concurrenttransfers` does. Do not pass `--transfers` expecting it to improve throughput.

To adjust parallelism:

```bash
git config lfs.concurrenttransfers 4
```

[concurrent]: https://github.com/git-lfs/git-lfs/blob/main/docs/man/git-lfs-config.5.ronn#upload-and-download-transfer-settings

## Rclone flags

Arguments not recognized by lfsrclone are passed directly to `rclone rcd` at daemon startup and apply for the entire session.

The following flags are set automatically — do not pass them:

| Flag | Reason |
|------|--------|
| `--rc-addr` | lfsrclone selects a free loopback port |
| `--rc-no-auth` | no authentication needed on loopback |
| `--ask-password=false` | prevents rclone from blocking on a password prompt |

The following behavior is configured per-transfer via the RC API `_config` parameter and cannot be overridden with flags:

- **`SizeOnly: true`** — skips ModTime checks (equivalent to `--size-only`)

## Known limitations

- **No file locking.** See [git-lfs #4314](https://github.com/git-lfs/git-lfs/issues/4314#issuecomment-730434427).

## Contributing

```bash
# Format and lint before committing
ruff format lfsrclone.py tests/
ruff check lfsrclone.py tests/

# Run the integration tests (requires git, git-lfs, and rclone on PATH)
pytest tests/ -v
```
