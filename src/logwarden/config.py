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

KNOWN_KEYS = {"fields", "baseline", "disable", "upstreams"}
DEFAULT_FIELDS = "logwarden-fields.yaml"
DEFAULT_BASELINE = ".logwarden-baseline.json"


@dataclass
class Config:
    fields: str | None = None
    baseline: str | None = None
    disable: dict[str, str] = field(default_factory=dict)
    upstreams: list[str] = field(default_factory=list)
    source: str | None = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)   # fatal: exit 2


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
    for key in ("fields", "baseline"):
        if isinstance(data.get(key), str):
            setattr(cfg, key, str(cwd / data[key]))
        elif key in data:
            cfg.warnings.append(f"{cfg.source}: '{key}' must be a path string")
    if "disable" in data:
        _disable(data["disable"], cfg)
    if "upstreams" in data:
        ups = data["upstreams"]
        if isinstance(ups, list) and all(isinstance(u, str) and u.strip() for u in ups):
            cfg.upstreams = [u.strip() for u in ups]
        else:
            cfg.errors.append(f'{cfg.source}: \'upstreams\' must be a list of host names, '
                              'e.g. upstreams = ["api.openai.com"]')
    return cfg


def _disable(value, cfg: Config) -> None:
    from logwarden import data as lwdata

    example = 'disable = { adr_non_json_line = "why this rule is off" }'
    if not isinstance(value, dict):
        cfg.errors.append(f"{cfg.source}: 'disable' needs a reason per rule: {example}")
        return
    known = lwdata.rules()
    for rule, reason in value.items():
        if rule not in known:
            cfg.errors.append(f"{cfg.source}: disable: unknown rule '{rule}' (run: logwarden rules)")
        elif not isinstance(reason, str) or not reason.strip():
            cfg.errors.append(f"{cfg.source}: disable: rule '{rule}' needs a non-empty reason: {example}")
        else:
            cfg.disable[rule] = reason.strip()


def _read(p: Path, cfg: Config) -> dict | None:
    try:
        return tomllib.loads(p.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        cfg.warnings.append(f"{p.name}: could not read ({e})")
        return None
