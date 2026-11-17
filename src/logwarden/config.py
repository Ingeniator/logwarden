"""Project configuration: [tool.logwarden] in pyproject.toml, or logwarden.toml.

logwarden.toml uses the same keys at top level. If both exist, logwarden.toml wins.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

KNOWN_KEYS = {"fields"}
DEFAULT_FIELDS = "logwarden-fields.yaml"


@dataclass
class Config:
    fields: str | None = None
    source: str | None = None
    warnings: list[str] = field(default_factory=list)


def load(cwd: Path) -> Config:
    cfg = Config()
    data = None
    own = cwd / "logwarden.toml"
    pyproject = cwd / "pyproject.toml"
    if own.is_file():
        data, cfg.source = _read(own, cfg), own.name
        if pyproject.is_file() and "logwarden" in (_read(pyproject, cfg) or {}).get("tool", {}):
            cfg.warnings.append("both logwarden.toml and [tool.logwarden] found; using logwarden.toml")
    elif pyproject.is_file():
        tool = (_read(pyproject, cfg) or {}).get("tool", {})
        if "logwarden" in tool:
            data, cfg.source = tool["logwarden"], "pyproject.toml [tool.logwarden]"
    if not isinstance(data, dict):
        return cfg
    for key in sorted(set(data) - KNOWN_KEYS):
        cfg.warnings.append(f"{cfg.source}: unknown key '{key}' (known: {', '.join(sorted(KNOWN_KEYS))})")
    if isinstance(data.get("fields"), str):
        cfg.fields = str((cwd / data["fields"]))
    elif "fields" in data:
        cfg.warnings.append(f"{cfg.source}: 'fields' must be a path string")
    return cfg


def _read(p: Path, cfg: Config) -> dict | None:
    try:
        return tomllib.loads(p.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        cfg.warnings.append(f"{p.name}: could not read ({e})")
        return None
