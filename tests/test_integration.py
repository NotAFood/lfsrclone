import hashlib
import subprocess
from pathlib import Path


def _verify_remote(data_dir: Path, hh: str) -> None:
    p = data_dir / hh[:2] / hh[2:4] / hh
    assert p.exists(), f"Remote object missing: {hh}"
    assert hashlib.sha256(p.read_bytes()).hexdigest() == hh


def test_first_commit_uploaded_to_remote(lfs_env):
    _verify_remote(lfs_env["data_dir"], lfs_env["hh0"])


def test_second_commit_uploaded_to_remote(lfs_env):
    _verify_remote(lfs_env["data_dir"], lfs_env["hh1"])


def test_prune_clears_local_lfs_cache(lfs_env):
    repopath = lfs_env["repopath"]
    subprocess.check_call(
        ["git", "lfs", "prune", "--verify-remote", "--force"], cwd=repopath
    )
    cached = [p for p in (repopath / ".git" / "lfs").rglob("*") if p.is_file()]
    assert cached == []


def test_history_checkout_restores_correct_file(lfs_env):
    repopath = lfs_env["repopath"]
    f = repopath / "file1.ext"

    subprocess.check_call(["git", "checkout", "HEAD~1"], cwd=repopath)
    assert hashlib.sha256(f.read_bytes()).hexdigest() == lfs_env["hh0"]

    subprocess.check_call(["git", "checkout", "tip"], cwd=repopath)
    assert hashlib.sha256(f.read_bytes()).hexdigest() == lfs_env["hh1"]


def test_skip_smudge_clone_shows_pointer(lfs_env, skip_smudge_clone):
    f = skip_smudge_clone / "file1.ext"
    assert hashlib.sha256(f.read_bytes()).hexdigest() != lfs_env["hh1"]
    assert lfs_env["hh1"] in f.read_text()


def test_lfs_pull_downloads_file(lfs_env, pulled_clone):
    f = pulled_clone / "file1.ext"
    assert hashlib.sha256(f.read_bytes()).hexdigest() == lfs_env["hh1"]
    assert lfs_env["hh1"] not in f.read_text()


def test_checkout_old_version_on_clone(lfs_env, pulled_clone):
    subprocess.check_call(["git", "checkout", "HEAD~1"], cwd=pulled_clone)
    f = pulled_clone / "file1.ext"
    assert hashlib.sha256(f.read_bytes()).hexdigest() == lfs_env["hh0"]
