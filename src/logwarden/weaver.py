"""Find or download the OTel Weaver binary.

Order: $WEAVER, `weaver` on PATH, then a pinned release cached per user.
Downloads are checked against the release's published SHA-256.
"""
from __future__ import annotations

import hashlib
import io
import os
import platform
import shutil
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

WEAVER_VERSION = os.environ.get("WEAVER_VERSION", "v0.28.0")
_BASE = "https://github.com/open-telemetry/weaver/releases/download"


class WeaverError(RuntimeError):
    pass


def target() -> str:
    """Weaver release target triple for this machine."""
    machine = platform.machine().lower()
    arch = {"arm64": "aarch64", "aarch64": "aarch64", "x86_64": "x86_64", "amd64": "x86_64"}.get(machine)
    if arch is None:
        raise WeaverError(f"unsupported CPU '{machine}'; set WEAVER to a weaver binary")
    system = platform.system()
    if system == "Darwin":
        return f"{arch}-apple-darwin"
    if system == "Linux":
        return f"{arch}-unknown-linux-gnu"
    if system == "Windows" and arch == "x86_64":
        return "x86_64-pc-windows-msvc"
    raise WeaverError(f"unsupported platform {system}/{arch}; set WEAVER to a weaver binary")


def cache_dir() -> Path:
    if env := os.environ.get("LOGWARDEN_CACHE"):
        return Path(env)
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "logwarden"


def _download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as resp:  # noqa: S310 (fixed https URL)
        return resp.read()


def _stderr(msg: str) -> None:
    print(msg, file=sys.stderr)


def _safe_members(t: tarfile.TarFile):
    for m in t.getmembers():
        p = Path(m.name)
        if p.is_absolute() or ".." in p.parts or m.issym() or m.islnk():
            raise WeaverError(f"unsafe path in weaver archive: {m.name}")
        yield m


def _install(dest: Path, tgt: str, log=_stderr) -> Path:
    archive = f"weaver-{tgt}" + (".zip" if tgt.endswith("windows-msvc") else ".tar.xz")
    url = f"{_BASE}/{WEAVER_VERSION}/{archive}"
    log(f"logwarden: downloading weaver {WEAVER_VERSION} ({tgt})...")
    try:
        blob = _download(url)
        expected = _download(url + ".sha256").decode().split()[0].lower()
    except OSError as e:
        raise WeaverError(f"weaver download failed: {e}") from e
    actual = hashlib.sha256(blob).hexdigest()
    if actual != expected:
        raise WeaverError(f"weaver checksum mismatch for {archive}: {actual} != {expected}")

    tmp = dest.with_name(dest.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    if archive.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            z.extractall(tmp)
    else:
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:xz") as t:
            if sys.version_info >= (3, 12):
                t.extractall(tmp, filter="data")
            else:
                t.extractall(tmp, members=_safe_members(t))  # noqa: S202 (members checked)
    shutil.rmtree(dest, ignore_errors=True)
    tmp.rename(dest)
    return dest


def find(log=_stderr) -> str:
    """Path to a usable weaver binary, downloading it if needed."""
    if env := os.environ.get("WEAVER"):
        found = shutil.which(env) or (env if Path(env).is_file() else None)
        if not found:
            raise WeaverError(f"WEAVER={env} is not an executable")
        return found
    if on_path := shutil.which("weaver"):
        return on_path
    tgt = target()
    dest = cache_dir() / f"weaver-{WEAVER_VERSION}-{tgt}"
    exe = "weaver.exe" if tgt.endswith("windows-msvc") else "weaver"
    hits = list(dest.rglob(exe)) if dest.is_dir() else []
    if not hits:
        _install(dest, tgt, log)
        hits = list(dest.rglob(exe))
    if not hits:
        raise WeaverError(f"weaver binary not found in downloaded archive at {dest}")
    binary = hits[0]
    if sys.platform != "win32":
        binary.chmod(binary.stat().st_mode | 0o111)
    return str(binary)
