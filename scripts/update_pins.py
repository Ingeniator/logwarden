"""Update the pinned Weaver, semantic-conventions and GenAI semantic-conventions versions.

    python scripts/update_pins.py                       # the latest releases
    python scripts/update_pins.py --weaver v0.27.0      # a given version (or --semconv 1.44.0,
                                                        # --genai 0.2.0)

Rewrites the pins in src/logwarden/weaver.py, the registry manifest and docs/integration.md,
adding the SHA-256 of the semconv and GenAI release archives (downloaded and hashed here). A new
GenAI release can drop or rename fields: tests/test_genai.py then says which entries of
_data/registry/genai-deprecated.yaml to update. Weaver needs no
hash here: its downloads are checked against the SHA-256 the release publishes. Prints what
changed; writes `changed=true|false` and `title=...` to $GITHUB_OUTPUT when set.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEAVER_PY = ROOT / "src/logwarden/weaver.py"
MANIFEST = ROOT / "src/logwarden/_data/registry/manifest.yaml"
GUIDE = ROOT / "docs/integration.md"
API = "https://api.github.com/repos/open-telemetry/{repo}/releases/latest"
ARCHIVE = "https://github.com/open-telemetry/semantic-conventions/archive/refs/tags/v{v}.tar.gz"
GENAI_ARCHIVE = "https://github.com/open-telemetry/semantic-conventions-genai/archive/refs/tags/v{v}.tar.gz"


def _get(url: str) -> bytes:
    headers = {"User-Agent": "logwarden-update-pins", "Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN") and "api.github.com" in url:
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120) as r:
        return r.read()


def latest(repo: str) -> str:
    return json.loads(_get(API.format(repo=repo)))["tag_name"]


def current() -> tuple[str, str, str]:
    w = re.search(r'WEAVER_VERSION = os\.environ\.get\("WEAVER_VERSION", "(v[^"]+)"\)', WEAVER_PY.read_text(encoding="utf-8")).group(1)
    m = MANIFEST.read_text(encoding="utf-8")
    s = re.search(r"semantic-conventions@v([0-9.]+)\[model\]", m).group(1)
    g = re.search(r"semantic-conventions-genai@v([0-9.]+)\[model\]", m).group(1)
    return w, s, g


def set_weaver(old: str, new: str) -> None:
    WEAVER_PY.write_text(WEAVER_PY.read_text(encoding="utf-8").replace(f'"WEAVER_VERSION", "{old}"', f'"WEAVER_VERSION", "{new}"'), encoding="utf-8")
    GUIDE.write_text(GUIDE.read_text(encoding="utf-8").replace(f"Weaver binary ({old})", f"Weaver binary ({new})"), encoding="utf-8")


def set_semconv(old: str, new: str) -> None:
    sha = hashlib.sha256(_get(ARCHIVE.format(v=new))).hexdigest()
    text = WEAVER_PY.read_text(encoding="utf-8")
    if f'"{new}":' not in text:
        text = re.sub(r"(SEMCONV_SHA256 = \{\n)", rf'\1    "{new}": "{sha}",\n', text, count=1)
    WEAVER_PY.write_text(text, encoding="utf-8")
    MANIFEST.write_text(MANIFEST.read_text(encoding="utf-8").replace(f"schemas/{old}", f"schemas/{new}")
                        .replace(f"semantic-conventions@v{old}[", f"semantic-conventions@v{new}["), encoding="utf-8")
    GUIDE.write_text(GUIDE.read_text(encoding="utf-8").replace(f"semantic-conventions v{old}", f"semantic-conventions v{new}"), encoding="utf-8")
    print(f"semconv archive v{new}: sha256 {sha}")


def _schema_url(blob: bytes, version: str) -> str:
    """The schema_url in the GenAI archive's model/manifest.yaml (the dependency must name it)."""
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as t:
        f = t.extractfile(f"semantic-conventions-genai-{version}/model/manifest.yaml")
        return re.search(r"^schema_url:\s*(\S+)", f.read().decode("utf-8"), re.M).group(1)


def set_genai(old: str, new: str) -> None:
    blob = _get(GENAI_ARCHIVE.format(v=new))
    sha = hashlib.sha256(blob).hexdigest()
    text = WEAVER_PY.read_text(encoding="utf-8")
    if f'"{new}":' not in text:
        text = re.sub(r"(GENAI_SHA256 = \{\n)", rf'\1    "{new}": "{sha}",\n', text, count=1)
    WEAVER_PY.write_text(text, encoding="utf-8")
    m = MANIFEST.read_text(encoding="utf-8").replace(f"semantic-conventions-genai@v{old}[", f"semantic-conventions-genai@v{new}[")
    m = re.sub(r"(- schema_url:\s*)\S+(\n\s*registry_path:\s*\S*semantic-conventions-genai@)",
               lambda x: x.group(1) + _schema_url(blob, new) + x.group(2), m, count=1)
    MANIFEST.write_text(m, encoding="utf-8")
    GUIDE.write_text(GUIDE.read_text(encoding="utf-8").replace(f"semantic-conventions-genai v{old}", f"semantic-conventions-genai v{new}"), encoding="utf-8")
    print(f"GenAI semconv archive v{new}: sha256 {sha}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--weaver", help="Weaver version, e.g. v0.29.0 (default: latest release)")
    ap.add_argument("--semconv", help="semconv version, e.g. 1.46.0 (default: latest release)")
    ap.add_argument("--genai", help="GenAI semconv version, e.g. 0.2.0 (default: latest release)")
    a = ap.parse_args(argv)
    w_old, s_old, g_old = current()
    w_new = a.weaver or latest("weaver")
    s_new = (a.semconv or latest("semantic-conventions")).lstrip("v")
    g_new = (a.genai or latest("semantic-conventions-genai")).lstrip("v")
    if not w_new.startswith("v"):
        w_new = "v" + w_new
    changes = []
    if w_new != w_old:
        set_weaver(w_old, w_new)
        changes.append(f"Weaver {w_old} → {w_new}")
    if s_new != s_old:
        set_semconv(s_old, s_new)
        changes.append(f"semconv {s_old} → {s_new}")
    if g_new != g_old:
        set_genai(g_old, g_new)
        changes.append(f"GenAI semconv {g_old} → {g_new}")
    title = "Pins: " + ", ".join(changes) if changes else ""
    print(title or f"pins are current: Weaver {w_old}, semconv {s_old}, GenAI semconv {g_old}")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"changed={'true' if changes else 'false'}\ntitle={title}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
