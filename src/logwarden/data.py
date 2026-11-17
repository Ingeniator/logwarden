"""Paths to files shipped with logwarden (rules, registry, templates, docs)."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parent / "_data"
# In a source checkout the docs live at the repo root; wheels copy them into _data/docs.
_REPO_DOCS = Path(__file__).resolve().parents[2] / "docs"


def path(name: str) -> Path:
    """Absolute path of a shipped file, e.g. path("policies")."""
    return DATA / name


def doc(name: str) -> str:
    """Text of a shipped doc: "standard.md" or "rules.md"."""
    for base in (DATA / "docs", _REPO_DOCS):
        p = base / name
        if p.is_file():
            return p.read_text(encoding="utf-8")
    raise FileNotFoundError(f"logwarden doc not found: {name}")


@lru_cache(maxsize=None)
def rules() -> dict:
    """Rule id -> {section, title, hint, doc}."""
    return json.loads(path("rules.json").read_text(encoding="utf-8"))
