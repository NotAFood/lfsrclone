# Plan: Switch from rclone subprocess to rclone RC API

## Motivation

The current implementation spawns a new `rclone copy` subprocess for every
upload and download.  For cloud backends (S3, B2, Google Drive, etc.) this
means a fresh OAuth/credential handshake and a new TCP connection pool on
every file.  With many LFS objects, this multiplies API calls, latency, and
cost.

The rclone [Remote Control API](https://rclone.org/rc/) (`rclone rcd`) runs
rclone as a persistent HTTP daemon.  File operations are issued as HTTP POST
requests to `localhost`.  A single daemon handles all transfers in a session,
reusing credentials and connections across the LFS `loop()` lifecycle.

---

## High-Level Architecture

```
git-lfs  <--JSON-->  lfsrclone.py  <--HTTP POST-->  rclone rcd (localhost)
                         |
                    starts/stops rcd
                    on init/terminate
```

The LFS `init` event starts the daemon.  Each `upload`/`download` event
issues an HTTP request.  The LFS `terminate` event stops the daemon via
`core/quit`.

---

## Daemon Lifecycle

### Starting (`Main.init`)

```python
import subprocess, urllib.request, json, time

def _start_rcd(self) -> None:
    """Start rclone rcd and wait until it is ready."""
    cmd = [
        self.args.rclone_exe, "rcd",
        "--rc-addr", "localhost:0",   # OS picks a free port
        "--rc-no-auth",
        "--ask-password=false",
        *self.rclone_args,
    ]
    self.rcd_proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    # rclone prints "Serving remote control on http://127.0.0.1:<PORT>/"
    # to stderr; parse the port from the first line.
    assert self.rcd_proc.stderr is not None
    for raw in self.rcd_proc.stderr:
        line = raw.decode(errors="backslashreplace")
        if "Serving remote control on" in line:
            # e.g. "Serving remote control on http://127.0.0.1:52341/"
            url = line.split()[-1].rstrip("/")
            self.rc_url = url
            break
    else:
        logging.critical("rclone rcd failed to start")
        sys.exit(1)
```

Key choices:
- `--rc-addr localhost:0` lets the OS assign a free port, avoiding conflicts.
- `--rc-no-auth` keeps the implementation stdlib-only (no credential management).
- Pass `self.rclone_args` through so user flags (e.g. `--config`, `--transfers`)
  still apply.

### Stopping (`Main.loop` / `terminate` event)

```python
def _stop_rcd(self) -> None:
    try:
        self._rc("core/quit", {})
    except Exception:
        pass  # daemon may already be gone
    if hasattr(self, "rcd_proc"):
        self.rcd_proc.wait(timeout=5)
```

---

## HTTP Helper

All RC calls use the same pattern: HTTP POST with a JSON body to
`<rc_url>/<endpoint>`.  A small helper keeps this DRY and stdlib-only
(`urllib.request`):

```python
def _rc(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
    url = f"{self.rc_url}/{endpoint}"
    data = json.dumps(params).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())
```

No third-party dependencies — `urllib.request` and `json` are stdlib.

---

## File Transfer (`Main.action`)

### Upload

Replace `rclone copy <local_path> <remote_dir>` with:

```
POST /operations/copyfile
{
  "srcFs":  {"type": "local", "_root": "<dir of local file>"},
  "srcRemote": "<filename>",
  "dstFs":  "<remote>:<prefix>",
  "dstRemote": "<oid>",
  "_group": "lfs/<oid>",
  "_async": true
}
```

`operations/copyfile` copies a single named file rather than a directory,
which avoids the need for `--no-traverse`.  `_async: true` returns immediately
with a `jobid`; we poll for progress separately.

### Download

Replace `rclone copy <remote_path> <local_dir>` with:

```
POST /operations/copyfile
{
  "srcFs":     "<remote>:<prefix>",
  "srcRemote": "<oid>",
  "dstFs":     {"type": "local", "_root": "<temp_dir>"},
  "dstRemote": "<oid>",
  "_group": "lfs/<oid>",
  "_async": true
}
```

---

## Progress Polling

With the subprocess approach, progress came from parsing rclone's JSON log
on stderr.  With the RC approach, poll `core/stats` with the per-transfer
group name:

```python
def _poll_progress(self, jobid: int, oid: str, size: int) -> None:
    group = f"lfs/{oid}"
    prev = 0
    while True:
        status = self._rc("job/status", {"jobid": jobid})
        if not status.get("finished"):
            stats = self._rc("core/stats", {"group": group})
            transferring = stats.get("transferring", [{}])
            if transferring:
                stat_bytes = transferring[0].get("bytes", 0)
                write({
                    "event": "progress",
                    "oid": oid,
                    "bytesSoFar": stat_bytes,
                    "bytesSinceLast": stat_bytes - prev,
                })
                prev = stat_bytes
            time.sleep(0.1)
        else:
            break
    # Final 100% progress
    write({"event": "progress", "oid": oid,
           "bytesSoFar": size, "bytesSinceLast": size - prev})
```

Poll interval of 100 ms matches rclone's default stats interval.  For finer
control the user can pass `--stats 50ms` via `rclone_args`.

---

## Error Handling

`job/status` returns `"success": false` and `"error": "<msg>"` on failure.
Map this to the existing LFS `complete` error format:

```python
complete: dict[str, Any] = {"event": "complete", "oid": oid}
if not status.get("success"):
    complete["error"] = {"code": 1, "message": status.get("error", "unknown")}
```

---

## Class-Level Changes

New instance attributes (add to class body annotations):

```python
rcd_proc: subprocess.Popen[bytes]
rc_url: str
```

New import:

```python
import time
import urllib.request
```

(`urllib.request` is stdlib — no new third-party dependencies.)

---

## Migration Impact

| Aspect | Before | After |
|---|---|---|
| rclone invocations per transfer | 1 subprocess | 1 HTTP POST |
| Credential handshake | Every transfer | Once per session |
| Connection pool | Recreated each transfer | Persistent |
| Progress source | stderr JSON log | `core/stats` HTTP poll |
| Termination | `sys.exit()` | `core/quit` + `sys.exit()` |
| New imports | — | `time`, `urllib.request` |
| Third-party deps | none | none (unchanged) |

---

## Open Questions

1. **Port conflict robustness**: `--rc-addr localhost:0` relies on parsing
   rclone's startup log line.  If the log format changes between rclone
   versions, the port detection breaks.  Alternative: pick a random port
   ourselves with `socket.bind(('', 0))` before launching.

2. **`--size-only` equivalent**: The current code passes `--size-only` to skip
   ModTime checks.  With `operations/copyfile` this must go in `_config`:
   `"_config": {"SizeOnly": true}`.  Verify this key name against
   `rclone rc --loopback options/get`.

3. **Concurrent transfers**: git-lfs may invoke lfsrclone with
   `lfs.concurrenttransfers > 1`, meaning multiple instances of lfsrclone run
   in parallel.  Each instance would start its own `rcd` daemon.  That's fine
   (each gets its own port), but worth confirming it doesn't hit per-user
   API rate limits worse than the current approach.

4. **`--ask-password=false`**: Currently passed as a CLI flag.  With rcd it
   must be included in the `rcd` startup command (already accounted for above).

5. **Cleanup on crash**: If lfsrclone is killed mid-session, `rcd` will be
   left running.  Add `atexit.register(self._stop_rcd)` or a `signal` handler
   to clean up.

---

## Implementation Order

1. Add `_start_rcd`, `_rc`, `_stop_rcd`, `_poll_progress` as private helpers.
2. Update `init()` to call `_start_rcd()`.
3. Rewrite `action()` to call `_rc("operations/copyfile", ...)` and
   `_poll_progress(...)` instead of `subprocess.Popen`.
4. Update `loop()` `terminate` branch to call `_stop_rcd()` before
   `sys.exit()`.
5. Add `time` and `urllib.request` imports.
6. Add `--rc-stats-interval` CLI flag (optional, defaults to `100ms`) to let
   users tune progress granularity without editing rclone_args.
7. Update `AGENTS.md` and `README.md` to reflect new architecture.
8. Run full integration test suite — tests exercise upload/download/prune/pull
   against a real rclone local alias remote, so they will catch regressions.
