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

KNOWN_KEYS = {"fields", "baseline", "disable", "upstreams", "events", "source", "profile"}
SOURCE_KEYS = {"format", "message_field", "ignore_fields", "service_field"}
DEFAULT_FIELDS = "logwarden-fields.yaml"
DEFAULT_BASELINE = ".logwarden-baseline.json"


@dataclass
class Config:
    fields: str | None = None
    baseline: str | None = None
    events: str | None = None           # event catalog file (TOML)
    disable: dict[str, str] = field(default_factory=dict)
    upstreams: list[str] = field(default_factory=list)
    input: dict = field(default_factory=dict)   # [source]: format, message_field, ignore_fields, service_field
    profile: str | None = None          # rule profile: essential or standard
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
    for key in ("fields", "baseline", "events"):
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
    if "source" in data:
        _source(data["source"], cfg)
    if "profile" in data:
        from logwarden.check import PROFILES
        if data["profile"] in PROFILES:
            cfg.profile = data["profile"]
        else:
            cfg.errors.append(f"{cfg.source}: profile must be one of {', '.join(PROFILES)}")
    return cfg


def _source(value, cfg: Config) -> None:
    from logwarden.sources import FORMATS
    where = f"{cfg.source}: [source]"
    if not isinstance(value, dict):
        cfg.errors.append(f"{where} must be a table, e.g. [source] message_field = \"log\"")
        return
    for key in sorted(set(value) - SOURCE_KEYS):
        cfg.warnings.append(f"{where}: unknown key '{key}' (known: {', '.join(sorted(SOURCE_KEYS))})")
    for key in ("message_field", "service_field", "format"):
        if key in value:
            if isinstance(value[key], str) and value[key].strip():
                cfg.input[key] = value[key].strip()
            else:
                cfg.errors.append(f"{where}: '{key}' must be a non-empty string")
    if cfg.input.get("format", "auto") not in FORMATS:
        cfg.errors.append(f"{where}: format must be one of {', '.join(FORMATS)}")
    if "ignore_fields" in value:
        v = value["ignore_fields"]
        if isinstance(v, list) and all(isinstance(x, str) and x.strip() for x in v):
            cfg.input["ignore_fields"] = [x.strip() for x in v]
        else:
            cfg.errors.append(f'{where}: \'ignore_fields\' must be a list of field names, '
                              'e.g. ignore_fields = ["kubernetes", "@timestamp"]')


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
