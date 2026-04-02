#!/usr/bin/env python
"""
lfsrclone: rclone-based transfer agent for git-lfs
"""

from __future__ import annotations

__version__ = "20260401.0.BETA"

import argparse
import atexit
import json
import logging
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from typing import Any


def write(msg: dict[str, Any] | None = None) -> None:
    if not msg:
        msg = {}
    print(json.dumps(msg), flush=True)
    logging.debug("write msg %s", msg)


def read() -> dict[str, Any]:
    line = sys.stdin.readline()
    msg = json.loads(line)
    logging.debug("read msg %s", msg)
    return msg


class Main:
    args: argparse.Namespace
    rclone_args: list[str]
    c: int
    rcd_proc: subprocess.Popen[bytes]
    rc_url: str

    def __init__(self, argv: list[str] | None = None) -> None:
        if argv is None:
            argv = sys.argv[1:]

        parser = argparse.ArgumentParser(
            allow_abbrev=False,  # to avoid prefix matching
            epilog="""
                All additional arguments are passed to rclone rcd
            """,
        )
        parser.add_argument("remote", help="Specify rclone remote")

        parser.add_argument(
            "--log-file",
            default=".git/lfsrclone.log",
            help="[%(default)s] Specify alternative log file destination",
        )
        parser.add_argument(
            "--log-level",
            help="Logging levels. Set to None to (effectivly) disable logging (i.e. set to 9999)",
            default="WARNING",  # Also the python default
            choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL", "NONE"],
        )
        parser.add_argument(
            "--rclone-exe",
            default="rclone",
            help="['rclone'] Specify rclone executable.",
        )
        parser.add_argument(
            "--temp-dir",
            default=".git/lfsrclone-tmp",
            help="[%(default)s] Specify a temporary download directory",
        )
        parser.add_argument(
            "--rc-stats-interval",
            default=0.1,
            type=float,
            help="[%(default)s] Polling interval in seconds for RC API progress updates",
        )

        args, rclone_args = parser.parse_known_args(argv)
        self.args = args
        self.rclone_args = rclone_args

        if args.log_level == "NONE":
            log_level: int = 9999
        else:
            log_level = getattr(logging, args.log_level)

        logging.basicConfig(
            filename=args.log_file,
            encoding="utf-8",
            format="%(levelname)s:%(asctime)s: %(message)s",
            level=log_level,
        )

        logging.debug("argv: %s", argv)
        logging.debug("args: %s", args)
        logging.debug("rclone: %s", rclone_args)

    def run(self) -> None:
        self.init()
        self.loop()

    def _start_rcd(self) -> None:
        # Reserve a port; TOCTOU window is negligible on loopback.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        self.rc_url = f"http://127.0.0.1:{port}"
        cmd = [
            self.args.rclone_exe,
            "rcd",
            "--rc-addr",
            f"127.0.0.1:{port}",
            "--rc-no-auth",
            "--ask-password=false",
            *self.rclone_args,
        ]
        logging.debug("Starting rcd: %s", cmd)
        self.rcd_proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        atexit.register(self._stop_rcd)

        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                self._rc("rc/noop", {})
                logging.info("rclone rcd ready at %s", self.rc_url)
                return
            except OSError:
                time.sleep(0.05)

        logging.critical("rclone rcd failed to become ready within 30 s")
        self.rcd_proc.terminate()
        sys.exit(1)

    def _stop_rcd(self) -> None:
        try:
            self._rc("core/quit", {})
        except OSError:
            pass  # daemon may already be gone
        if hasattr(self, "rcd_proc"):
            try:
                self.rcd_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.rcd_proc.terminate()

    def _rc(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.rc_url}/{endpoint}"
        data = json.dumps(params).encode()
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())  # type: ignore[no-any-return]

    def _poll_progress(self, jobid: int, oid: str, size: int) -> dict[str, Any]:
        group = f"lfs/{oid}"
        prev = 0
        interval = self.args.rc_stats_interval
        status: dict[str, Any] = {}
        while True:
            status = self._rc("job/status", {"jobid": jobid})
            if status.get("finished"):
                break
            stats = self._rc("core/stats", {"group": group})
            transferring = stats.get("transferring") or []
            if transferring:
                stat_bytes = int(transferring[0].get("bytes", 0))
                write(
                    {
                        "event": "progress",
                        "oid": oid,
                        "bytesSoFar": stat_bytes,
                        "bytesSinceLast": stat_bytes - prev,
                    }
                )
                prev = stat_bytes
            time.sleep(interval)

        # Final 100% progress
        write(
            {
                "event": "progress",
                "oid": oid,
                "bytesSoFar": size,
                "bytesSinceLast": size - prev,
            }
        )
        return status

    def init(self) -> None:
        msg = read()
        if msg["event"] != "init":
            logging.critical('Incorrect msg type. Expected "init". Got %s', msg)
            sys.exit(1)
        self._start_rcd()
        logging.info("Initiated in %s", os.getcwd())
        write()

    def loop(self) -> None:
        self.c = 0
        while True:
            logging.info("Loop %i", self.c)
            msg = read()
            if msg["event"] in {"upload", "download"}:
                self.action(msg)
            elif msg["event"] == "terminate":
                logging.info("Termination Called")
                self._stop_rcd()
                sys.exit()
            else:
                logging.critical("Recieved incorrect event %s", msg["event"])
                sys.exit(1)
            self.c += 1

    def action(self, msg: dict[str, Any]) -> None:
        oid, size = msg["oid"], msg["size"]
        dst = ""

        if msg["event"] == "upload":
            src_path = os.path.abspath(msg["path"])
            params: dict[str, Any] = {
                "srcFs": os.path.dirname(src_path),
                "srcRemote": os.path.basename(src_path),
                "dstFs": pathjoin(self.args.remote, f"{oid[:2]}/{oid[2:4]}"),
                "dstRemote": oid,
                "_group": f"lfs/{oid}",
                "_async": True,
                "_config": {"SizeOnly": True},
            }
        elif msg["event"] == "download":
            dst = self.args.temp_dir or tempfile.mkdtemp()
            params = {
                "srcFs": pathjoin(self.args.remote, f"{oid[:2]}/{oid[2:4]}"),
                "srcRemote": oid,
                "dstFs": dst,
                "dstRemote": oid,
                "_group": f"lfs/{oid}",
                "_async": True,
                "_config": {"SizeOnly": True},
            }
        else:
            logging.critical("Unrecognized event")
            sys.exit(1)

        logging.debug("RC copyfile params: %s", params)
        result = self._rc("operations/copyfile", params)
        jobid = int(result["jobid"])
        logging.debug("Job %d started for oid %s", jobid, oid)

        final_status = self._poll_progress(jobid, oid, size)

        complete: dict[str, Any] = {"event": "complete", "oid": oid}
        if msg["event"] == "download":
            complete["path"] = f"{dst}/{oid}"
        if not final_status.get("success"):
            complete["error"] = {
                "code": 1,
                "message": final_status.get("error", "unknown error"),
            }
            logging.debug(
                "Transfer error for oid %s: %s", oid, final_status.get("error")
            )
        write(complete)
        logging.debug("Action %s complete: %s", self.c, msg)


def main() -> None:
    Main().run()


def pathjoin(*args: str) -> str:
    """
    This is like os.path.join but does some rclone-specific things because there could be
    a ':' in the first part.

    The second argument could be '/file', or 'file' and the first could have a colon.
        pathjoin('a','b')   # a/b
        pathjoin('a:','b')  # a:b
        pathjoin('a:','/b') # a:/b
        pathjoin('a','/b')  # a/b  NOTE that this is different than os.path.join
    """
    if len(args) <= 1:
        return "".join(args)

    root, first, rest = args[0], args[1], args[2:]

    if root.endswith("/"):
        root = root[:-1]

    if root.endswith(":") or first.startswith("/"):
        path = root + first
    else:
        path = f"{root}/{first}"

    path = os.path.join(path, *rest)
    return path


if __name__ == "__main__":
    main()
