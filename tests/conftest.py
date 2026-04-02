import hashlib
import os
import shlex
import subprocess
from pathlib import Path

import pytest

LFSRC = str(Path(__file__).parent.parent / "lfsrclone.py")


@pytest.fixture(scope="session", autouse=True)
def check_required_tools():
    for cmd in [["git", "version"], ["git", "lfs", "version"], ["rclone", "version"]]:
        try:
            subprocess.check_call(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            pytest.skip(f"Required tool not available: {' '.join(cmd)}")


def _configure_lfsrclone(repopath: Path, lfsrclone_args: str) -> None:
    for key, val in [
        ("lfs.customtransfer.lfsrclone.path", LFSRC),
        ("lfs.standalonetransferagent", "lfsrclone"),
        ("lfs.customtransfer.lfsrclone.args", lfsrclone_args),
    ]:
        subprocess.check_call(["git", "config", "--add", key, val], cwd=repopath)


def _git_identity(repopath: Path) -> None:
    subprocess.check_call(
        ["git", "config", "user.email", "test@example.com"], cwd=repopath
    )
    subprocess.check_call(["git", "config", "user.name", "Test"], cwd=repopath)


@pytest.fixture(scope="session")
def lfs_env(tmp_path_factory):
    base = tmp_path_factory.mktemp("lfsrclone")

    data_dir = base / "data"
    data_dir.mkdir()

    cfg = base / "cfg"
    cfg.write_text(f"[remote]\ntype = alias\nremote = {data_dir}\n")

    lfsrclone_args = shlex.join(
        [
            "remote:",
            "--stats",
            "100ms",
            "--config",
            str(cfg),
            "--log-level",
            "DEBUG",
            "--log-file",
            str(base / "lfsrclone.log"),
        ]
    )

    subprocess.check_call(["git", "init", "--bare", "host"], cwd=base)
    hostpath = base / "host"

    subprocess.check_call(["git", "clone", str(hostpath), "repo"], cwd=base)
    repopath = base / "repo"
    _git_identity(repopath)
    _configure_lfsrclone(repopath, lfsrclone_args)

    subprocess.check_call(["git", "lfs", "track", "*.ext"], cwd=repopath)
    subprocess.check_call(["git", "add", ".gitattributes"], cwd=repopath)

    f = repopath / "file1.ext"
    f.write_text("line 1\n")
    subprocess.check_call(["git", "add", "file1.ext"], cwd=repopath)
    subprocess.check_call(["git", "commit", "-m", "file1"], cwd=repopath)
    subprocess.check_call(["git", "push"], cwd=repopath)
    hh0 = hashlib.sha256(f.read_bytes()).hexdigest()

    with f.open("a") as fout:
        fout.write("line 2\n")
    subprocess.check_call(["git", "add", "file1.ext"], cwd=repopath)
    subprocess.check_call(["git", "commit", "-m", "file1 line2"], cwd=repopath)
    subprocess.check_call(["git", "push"], cwd=repopath)
    hh1 = hashlib.sha256(f.read_bytes()).hexdigest()

    subprocess.check_call(["git", "tag", "tip"], cwd=repopath)

    yield {
        "base": base,
        "data_dir": data_dir,
        "hostpath": hostpath,
        "repopath": repopath,
        "lfsrclone_args": lfsrclone_args,
        "hh0": hh0,
        "hh1": hh1,
    }


@pytest.fixture(scope="session")
def skip_smudge_clone(lfs_env):
    env_vars = os.environ.copy()
    env_vars["GIT_LFS_SKIP_SMUDGE"] = "1"
    base = lfs_env["base"]

    subprocess.check_call(
        ["git", "clone", str(lfs_env["hostpath"]), "repo2"], cwd=base, env=env_vars
    )
    repopath2 = base / "repo2"
    _git_identity(repopath2)
    _configure_lfsrclone(repopath2, lfs_env["lfsrclone_args"])
    return repopath2


@pytest.fixture(scope="session")
def pulled_clone(lfs_env):
    env_vars = os.environ.copy()
    env_vars["GIT_LFS_SKIP_SMUDGE"] = "1"
    base = lfs_env["base"]

    subprocess.check_call(
        ["git", "clone", str(lfs_env["hostpath"]), "repo3"], cwd=base, env=env_vars
    )
    repopath3 = base / "repo3"
    _git_identity(repopath3)
    _configure_lfsrclone(repopath3, lfs_env["lfsrclone_args"])
    subprocess.check_call(["git", "lfs", "pull"], cwd=repopath3)
    return repopath3
