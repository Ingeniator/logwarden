"""Weaver and semconv download paths, without the network: platforms, checksums, archives, failures."""
import hashlib
import io
import tarfile
import zipfile

import pytest

from logwarden import weaver


def _tar(files: dict[str, bytes], mode="w:xz") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode=mode) as t:
        for name, body in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            t.addfile(info, io.BytesIO(body))
    return buf.getvalue()


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, body in files.items():
            z.writestr(name, body)
    return buf.getvalue()


def _serve(monkeypatch, blobs: dict[str, bytes]):
    """Fake _download: URL suffix -> bytes; anything else is an OSError (offline)."""
    def dl(url):
        for suffix, blob in blobs.items():
            if url.endswith(suffix):
                return blob
        raise OSError(f"offline: {url}")
    monkeypatch.setattr(weaver, "_download", dl)


@pytest.fixture
def no_weaver(tmp_path, monkeypatch):
    monkeypatch.delenv("WEAVER", raising=False)
    monkeypatch.setenv("LOGWARDEN_CACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(weaver.shutil, "which", lambda name: None)
    return tmp_path / "cache"


@pytest.mark.parametrize("system,machine,want", [
    ("Darwin", "arm64", "aarch64-apple-darwin"),
    ("Linux", "x86_64", "x86_64-unknown-linux-gnu"),
    ("Linux", "aarch64", "aarch64-unknown-linux-gnu"),
    ("Windows", "AMD64", "x86_64-pc-windows-msvc"),
])
def test_target(monkeypatch, system, machine, want):
    monkeypatch.setattr(weaver.platform, "system", lambda: system)
    monkeypatch.setattr(weaver.platform, "machine", lambda: machine)
    assert weaver.target() == want


@pytest.mark.parametrize("system,machine,msg", [
    ("Linux", "riscv64", "unsupported CPU"),
    ("Windows", "arm64", "unsupported platform"),
    ("FreeBSD", "amd64", "unsupported platform"),
])
def test_unsupported_target_says_set_weaver(monkeypatch, system, machine, msg):
    monkeypatch.setattr(weaver.platform, "system", lambda: system)
    monkeypatch.setattr(weaver.platform, "machine", lambda: machine)
    with pytest.raises(weaver.WeaverError, match=f"{msg}.*set WEAVER"):
        weaver.target()


@pytest.mark.parametrize("plat,env,want", [
    ("win32", {"LOCALAPPDATA": "C:/Users/u/AppData/Local"}, "C:/Users/u/AppData/Local/logwarden"),
    ("darwin", {}, "~/Library/Caches/logwarden"),
    ("linux", {"XDG_CACHE_HOME": "/xdg"}, "/xdg/logwarden"),
    ("linux", {}, "~/.cache/logwarden"),
])
def test_cache_dir_per_platform(monkeypatch, tmp_path, plat, env, want):
    for k in ("LOGWARDEN_CACHE", "LOCALAPPDATA", "XDG_CACHE_HOME"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(weaver.sys, "platform", plat)
    monkeypatch.setattr(weaver.Path, "home", classmethod(lambda cls: tmp_path))
    assert weaver.cache_dir().as_posix() == want.replace("~", tmp_path.as_posix())


def test_weaver_env_override(tmp_path, monkeypatch):
    exe = tmp_path / "my-weaver"
    exe.write_text("")
    monkeypatch.setenv("WEAVER", str(exe))
    assert weaver.find() == str(exe)
    monkeypatch.setenv("WEAVER", str(tmp_path / "missing"))
    with pytest.raises(weaver.WeaverError, match="is not an executable"):
        weaver.find()


def test_weaver_on_path_wins(monkeypatch):
    monkeypatch.delenv("WEAVER", raising=False)
    monkeypatch.setattr(weaver.shutil, "which", lambda name: "/usr/local/bin/weaver")
    assert weaver.find() == "/usr/local/bin/weaver"


def test_download_install_and_reuse(no_weaver, monkeypatch):
    tgt = "x86_64-unknown-linux-gnu"
    monkeypatch.setattr(weaver, "target", lambda: tgt)
    blob = _tar({f"weaver-{tgt}/weaver": b"#!/bin/sh\n"})
    _serve(monkeypatch, {".tar.xz": blob, ".sha256": f"{hashlib.sha256(blob).hexdigest()}  x".encode()})
    logs = []
    exe = weaver.find(log=logs.append)
    assert exe.endswith("weaver") and "downloading weaver" in logs[0]
    if weaver.sys.platform != "win32":
        assert weaver.os.access(exe, weaver.os.X_OK)        # made executable after unpacking
    _serve(monkeypatch, {})                                  # offline: the cached copy is used
    assert weaver.find(log=logs.append) == exe and len(logs) == 1
    assert not list(no_weaver.glob("*.tmp"))                 # unpacked atomically


def test_windows_zip_install(no_weaver, monkeypatch):
    tgt = "x86_64-pc-windows-msvc"
    monkeypatch.setattr(weaver, "target", lambda: tgt)
    monkeypatch.setattr(weaver.sys, "platform", "win32")
    blob = _zip({f"weaver-{tgt}/weaver.exe": b"MZ"})
    _serve(monkeypatch, {".zip": blob, ".sha256": hashlib.sha256(blob).hexdigest().encode()})
    assert weaver.find(log=lambda m: None).endswith("weaver.exe")


def test_checksum_mismatch_is_refused(no_weaver, monkeypatch):
    monkeypatch.setattr(weaver, "target", lambda: "x86_64-unknown-linux-gnu")
    _serve(monkeypatch, {".tar.xz": b"tampered", ".sha256": b"0" * 64})
    with pytest.raises(weaver.WeaverError, match="checksum mismatch"):
        weaver.find(log=lambda m: None)
    assert not list(no_weaver.rglob("weaver"))               # nothing installed


def test_offline_first_run_fails_clearly(no_weaver, monkeypatch):
    monkeypatch.setattr(weaver, "target", lambda: "x86_64-unknown-linux-gnu")
    _serve(monkeypatch, {})
    with pytest.raises(weaver.WeaverError, match="weaver download failed: offline"):
        weaver.find(log=lambda m: None)


def test_archive_without_binary(no_weaver, monkeypatch):
    monkeypatch.setattr(weaver, "target", lambda: "x86_64-unknown-linux-gnu")
    blob = _tar({"README.md": b"no binary here"})
    _serve(monkeypatch, {".tar.xz": blob, ".sha256": hashlib.sha256(blob).hexdigest().encode()})
    with pytest.raises(weaver.WeaverError, match="weaver binary not found"):
        weaver.find(log=lambda m: None)


@pytest.mark.parametrize("name", ["../evil", "/abs/evil", "C:/evil", "a/../../evil"])
def test_unsafe_archive_paths_rejected(name):
    with tarfile.open(fileobj=io.BytesIO(_tar({name: b"x"}, mode="w")), mode="r") as t:
        with pytest.raises(weaver.WeaverError, match="unsafe path"):
            list(weaver._safe_members(t))


def test_links_in_archive_skipped():
    """v0.10.2: Python < 3.12 skips links instead of failing (the GenAI archive has two)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        for name, kind in (("weaver", tarfile.SYMTYPE), ("hard", tarfile.LNKTYPE)):
            link = tarfile.TarInfo(name)
            link.type, link.linkname = kind, "/etc/passwd"
            t.addfile(link)
        f = tarfile.TarInfo("ok.txt")
        f.size = 1
        t.addfile(f, io.BytesIO(b"x"))
    with tarfile.open(fileobj=io.BytesIO(buf.getvalue()), mode="r") as t:
        assert [m.name for m in weaver._safe_members(t)] == ["ok.txt"]


def _semconv(monkeypatch, tmp_path, version, files):
    monkeypatch.setenv("LOGWARDEN_CACHE", str(tmp_path))
    monkeypatch.delenv("LOGWARDEN_SEMCONV", raising=False)
    blob = _tar(files, mode="w:gz")
    monkeypatch.setitem(weaver.SEMCONV_SHA256, version, hashlib.sha256(blob).hexdigest())
    _serve(monkeypatch, {f"v{version}.tar.gz": blob})


def test_semconv_download_and_cache(tmp_path, monkeypatch):
    _semconv(monkeypatch, tmp_path, "9.9.9", {"semantic-conventions-9.9.9/model/http.yaml": b"groups: []"})
    model = weaver.semconv_model("9.9.9", log=lambda m: None)
    assert (model / "http.yaml").is_file()
    _serve(monkeypatch, {})                                  # offline afterwards
    assert weaver.semconv_model("9.9.9") == model


def test_semconv_archive_without_model(tmp_path, monkeypatch):
    _semconv(monkeypatch, tmp_path, "9.9.8", {"semantic-conventions-9.9.8/README.md": b""})
    with pytest.raises(weaver.WeaverError, match="no model directory"):
        weaver.semconv_model("9.9.8", log=lambda m: None)


def test_semconv_offline_first_run(tmp_path, monkeypatch):
    monkeypatch.setenv("LOGWARDEN_CACHE", str(tmp_path))
    monkeypatch.delenv("LOGWARDEN_SEMCONV", raising=False)
    _serve(monkeypatch, {})
    with pytest.raises(weaver.WeaverError, match="download failed.*LOGWARDEN_SEMCONV"):
        weaver.semconv_model("1.45.0", log=lambda m: None)


def test_semconv_env_dir_and_manifest_without_dependency(tmp_path, monkeypatch):
    monkeypatch.setenv("LOGWARDEN_SEMCONV", str(tmp_path))
    assert weaver.semconv_model("1.45.0") == tmp_path
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text("name: x\n", encoding="utf-8")
    with pytest.raises(weaver.WeaverError, match="no semantic-conventions dependency"):
        weaver.semconv_version(manifest)
