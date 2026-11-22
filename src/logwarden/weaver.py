"""Find or download the OTel Weaver binary and the OTel semantic-conventions registry.

Weaver: $WEAVER, `weaver` on PATH, then a pinned release cached per user, checked against
the release's published SHA-256.
Semconv: $LOGWARDEN_SEMCONV (a local `model` directory), else the release archive of the
version pinned in the registry manifest, checked against the SHA-256 pinned here and cached,
so checks don't clone a git repository and work offline after the first run.
"""
from __future__ import annotations

import hashlib
import io
import os
import platform
import re
import shutil
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path, PureWindowsPath

WEAVER_VERSION = os.environ.get("WEAVER_VERSION", "v0.28.0")
_BASE = "https://github.com/open-telemetry/weaver/releases/download"
_SEMCONV_ARCHIVE = "https://github.com/open-telemetry/semantic-conventions/archive/refs/tags/v{v}.tar.gz"
# SHA-256 of the GitHub release archive, per semconv version (update with the manifest).
SEMCONV_SHA256 = {
    "1.45.0": "32f2ba6b5f38d631174a729b9ddc786dbd54b0e85e19a8889ce974cbe7e2197b",
}


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
    from logwarden import selflog
    selflog.emit("weaver_download_started", msg.removeprefix("logwarden: "))


def _stderr_semconv(msg: str) -> None:
    from logwarden import selflog
    selflog.emit("semconv_download_started", msg.removeprefix("logwarden: "))


def _safe_members(t: tarfile.TarFile):
    for m in t.getmembers():
        # Windows rules are the strictest: they also catch "\\x", "C:/x" and "C:x" on any OS.
        p = PureWindowsPath(m.name)
        if p.anchor or ".." in p.parts or m.issym() or m.islnk():
            raise WeaverError(f"unsafe path in archive: {m.name}")
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

    return _unpack(blob, "zip" if archive.endswith(".zip") else "r:xz", dest)


def _unpack(blob: bytes, mode: str, dest: Path) -> Path:
    """Extract an archive into dest atomically (via dest.tmp), rejecting unsafe paths."""
    tmp = dest.with_name(dest.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    if mode == "zip":
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            z.extractall(tmp)
    else:
        with tarfile.open(fileobj=io.BytesIO(blob), mode=mode) as t:
            if sys.version_info >= (3, 12):
                t.extractall(tmp, filter="data")
            else:
                t.extractall(tmp, members=_safe_members(t))  # noqa: S202 (members checked)
    shutil.rmtree(dest, ignore_errors=True)
    tmp.rename(dest)
    return dest


def semconv_version(manifest: Path) -> str:
    m = re.search(r"semantic-conventions@v([0-9][0-9.]*)\[model\]", manifest.read_text(encoding="utf-8"))
    if not m:
        raise WeaverError(f"no semantic-conventions dependency in {manifest}")
    return m.group(1)


def semconv_model(version: str, log=_stderr_semconv) -> Path:
    """Local semconv `model` directory for `version`, downloading and caching it if needed."""
    if env := os.environ.get("LOGWARDEN_SEMCONV"):
        p = Path(env)
        if not p.is_dir():
            raise WeaverError(f"LOGWARDEN_SEMCONV={env} is not a directory (a semconv 'model' dir)")
        return p
    dest = cache_dir() / f"semconv-{version}"
    model = dest / f"semantic-conventions-{version}" / "model"
    if model.is_dir():
        return model
    expected = SEMCONV_SHA256.get(version)
    if not expected:
        raise WeaverError(f"no pinned checksum for semantic conventions {version}; "
                          "set LOGWARDEN_SEMCONV to a local semconv 'model' directory")
    url = _SEMCONV_ARCHIVE.format(v=version)
    log(f"logwarden: downloading OTel semantic conventions v{version}...")
    try:
        blob = _download(url)
    except OSError as e:
        raise WeaverError(f"semantic conventions download failed: {e}; set LOGWARDEN_SEMCONV "
                          "to a local semconv 'model' directory to work offline") from e
    actual = hashlib.sha256(blob).hexdigest()
    if actual != expected:
        raise WeaverError(f"checksum mismatch for {url}: {actual} != {expected} (the archive "
                          "changed or the download was corrupted); set LOGWARDEN_SEMCONV to a "
                          "local semconv 'model' directory")
    _unpack(blob, "r:gz", dest)
    if not model.is_dir():
        raise WeaverError(f"semconv archive has no model directory at {model}")
    return model


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
    if sys.platform != "win32" and not os.access(binary, os.X_OK):
        try:  # a shared or read-only cache (e.g. the Docker image run as another user) is fine as is
            binary.chmod(binary.stat().st_mode | 0o111)
        except OSError as e:
            raise WeaverError(f"weaver at {binary} is not executable and can't be made so: {e}") from e
    return str(binary)
