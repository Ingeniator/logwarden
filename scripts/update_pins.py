"""Update the pinned Weaver and semantic-conventions versions.

    python scripts/update_pins.py                       # the latest releases
    python scripts/update_pins.py --weaver v0.27.0      # a given version (or --semconv 1.44.0)

Rewrites the pins in src/logwarden/weaver.py, the registry manifest and docs/integration.md,
adding the SHA-256 of the semconv release archive (downloaded and hashed here). Weaver needs no
hash here: its downloads are checked against the SHA-256 the release publishes. Prints what
changed; writes `changed=true|false` and `title=...` to $GITHUB_OUTPUT when set.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEAVER_PY = ROOT / "src/logwarden/weaver.py"
MANIFEST = ROOT / "src/logwarden/_data/registry/manifest.yaml"
GUIDE = ROOT / "docs/integration.md"
API = "https://api.github.com/repos/open-telemetry/{repo}/releases/latest"
ARCHIVE = "https://github.com/open-telemetry/semantic-conventions/archive/refs/tags/v{v}.tar.gz"


def _get(url: str) -> bytes:
    headers = {"User-Agent": "logwarden-update-pins", "Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN") and "api.github.com" in url:
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120) as r:
        return r.read()


def latest(repo: str) -> str:
    return json.loads(_get(API.format(repo=repo)))["tag_name"]


def current() -> tuple[str, str]:
    w = re.search(r'WEAVER_VERSION = os\.environ\.get\("WEAVER_VERSION", "(v[^"]+)"\)', WEAVER_PY.read_text()).group(1)
    s = re.search(r"semantic-conventions@v([0-9.]+)\[model\]", MANIFEST.read_text()).group(1)
    return w, s


def set_weaver(old: str, new: str) -> None:
    WEAVER_PY.write_text(WEAVER_PY.read_text().replace(f'"WEAVER_VERSION", "{old}"', f'"WEAVER_VERSION", "{new}"'))
    GUIDE.write_text(GUIDE.read_text().replace(f"Weaver binary ({old})", f"Weaver binary ({new})"))


def set_semconv(old: str, new: str) -> None:
    sha = hashlib.sha256(_get(ARCHIVE.format(v=new))).hexdigest()
    text = WEAVER_PY.read_text()
    if f'"{new}":' not in text:
        text = re.sub(r"(SEMCONV_SHA256 = \{\n)", rf'\1    "{new}": "{sha}",\n', text, count=1)
    WEAVER_PY.write_text(text)
    MANIFEST.write_text(MANIFEST.read_text().replace(f"schemas/{old}", f"schemas/{new}")
                        .replace(f"semantic-conventions@v{old}[", f"semantic-conventions@v{new}["))
    GUIDE.write_text(GUIDE.read_text().replace(f"semantic-conventions v{old}", f"semantic-conventions v{new}"))
    print(f"semconv archive v{new}: sha256 {sha}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--weaver", help="Weaver version, e.g. v0.29.0 (default: latest release)")
    ap.add_argument("--semconv", help="semconv version, e.g. 1.46.0 (default: latest release)")
    a = ap.parse_args(argv)
    w_old, s_old = current()
    w_new = a.weaver or latest("weaver")
    s_new = (a.semconv or latest("semantic-conventions")).lstrip("v")
    if not w_new.startswith("v"):
        w_new = "v" + w_new
    changes = []
    if w_new != w_old:
        set_weaver(w_old, w_new)
        changes.append(f"Weaver {w_old} → {w_new}")
    if s_new != s_old:
        set_semconv(s_old, s_new)
        changes.append(f"semconv {s_old} → {s_new}")
    title = "Pins: " + ", ".join(changes) if changes else ""
    print(title or f"pins are current: Weaver {w_old}, semconv {s_old}")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"changed={'true' if changes else 'false'}\ntitle={title}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
