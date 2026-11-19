"""v0.5.0 claim 13: the semconv registry is downloaded once, verified, cached; then offline."""
import os
import shutil

import pytest

from conftest import EXAMPLES
from logwarden import data, weaver


def test_version_from_manifest():
    assert weaver.semconv_version(data.path("registry/manifest.yaml")) in weaver.SEMCONV_SHA256


def test_corrupted_archive_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("LOGWARDEN_CACHE", str(tmp_path))
    monkeypatch.delenv("LOGWARDEN_SEMCONV", raising=False)
    monkeypatch.setattr(weaver, "_download", lambda url: b"not the archive")
    with pytest.raises(weaver.WeaverError, match="checksum mismatch"):
        weaver.semconv_model("1.45.0", log=lambda m: None)
    assert not any(tmp_path.iterdir())  # nothing half-installed


def test_unpinned_version_and_bad_env(tmp_path, monkeypatch):
    monkeypatch.setenv("LOGWARDEN_CACHE", str(tmp_path))
    monkeypatch.delenv("LOGWARDEN_SEMCONV", raising=False)
    with pytest.raises(weaver.WeaverError, match="no pinned checksum"):
        weaver.semconv_model("0.0.1")
    monkeypatch.setenv("LOGWARDEN_SEMCONV", str(tmp_path / "nope"))
    with pytest.raises(weaver.WeaverError, match="not a directory"):
        weaver.semconv_model("1.45.0")


@pytest.mark.weaver
def test_offline_after_first_run_and_env_override(cli, tmp_path):
    cache = tmp_path / "cache"
    env = {"LOGWARDEN_CACHE": str(cache)}
    first = cli("check", EXAMPLES / "good.jsonl", cwd=tmp_path, env=env)
    assert first.returncode == 0, first.stderr
    assert "downloading OTel semantic conventions" in first.stderr or not first.stderr  # weaver may be on PATH
    blocked = {**env, "HTTPS_PROXY": "http://127.0.0.1:9", "HTTP_PROXY": "http://127.0.0.1:9",
               "ALL_PROXY": "http://127.0.0.1:9", "https_proxy": "http://127.0.0.1:9",
               "http_proxy": "http://127.0.0.1:9", "all_proxy": "http://127.0.0.1:9", "NO_PROXY": ""}
    offline = cli("check", EXAMPLES / "good.jsonl", cwd=tmp_path, env=blocked)
    assert offline.returncode == 0, offline.stderr
    assert "downloading" not in offline.stderr

    model = next(cache.glob("semconv-*/semantic-conventions-*/model"))
    binary = os.environ.get("WEAVER") or shutil.which("weaver") or next(
        str(p) for p in cache.rglob("weaver*") if p.is_file() and p.name in ("weaver", "weaver.exe"))
    other = {**blocked, "LOGWARDEN_CACHE": str(tmp_path / "empty"), "LOGWARDEN_SEMCONV": str(model),
             "WEAVER": binary}
    r = cli("check", EXAMPLES / "bad.jsonl", cwd=tmp_path, env=other)
    assert r.returncode == 1 and "downloading" not in r.stderr


def test_read_only_cache_with_executable_weaver(tmp_path, monkeypatch):
    """The Docker image runs as any user over a root-owned cache: no chmod when already executable."""
    import sys
    if sys.platform == "win32":
        pytest.skip("POSIX permissions")
    monkeypatch.delenv("WEAVER", raising=False)
    monkeypatch.setenv("LOGWARDEN_CACHE", str(tmp_path))
    monkeypatch.setattr(weaver.shutil, "which", lambda name: None)
    exe = tmp_path / f"weaver-{weaver.WEAVER_VERSION}-{weaver.target()}" / "weaver"
    exe.parent.mkdir()
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o555)
    monkeypatch.setattr(type(exe), "chmod", lambda self, mode: (_ for _ in ()).throw(PermissionError("read-only")))
    assert weaver.find() == str(exe)
