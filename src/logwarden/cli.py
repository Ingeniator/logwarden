"""logwarden command line. Exit codes: 0 clean, 1 violations, 2 usage or setup error."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from logwarden import __version__, check, config, data, output, weaver

FIELDS_DEFAULT = config.DEFAULT_FIELDS
_BEGIN, _END = "<!-- logwarden:begin -->", "<!-- logwarden:end -->"


class UsageError(Exception):
    pass


def _err(msg: str) -> None:
    print(f"logwarden: {msg}", file=sys.stderr)


def _read_input(src: str) -> str:
    if src == "-":
        return sys.stdin.buffer.read().decode("utf-8", errors="replace")
    p = Path(src)
    if not p.is_file():
        raise UsageError(f"check: no such file: {src}")
    return p.read_bytes().decode("utf-8", errors="replace")


def _load_config(cwd: Path) -> config.Config:
    cfg = config.load(cwd)
    for w in cfg.warnings:
        _err(f"warning: {w}")
    if cfg.errors:
        raise UsageError("\n".join(cfg.errors))
    return cfg


def _fields(arg: str | None, registry: str | None, cwd: Path, cfg: config.Config) -> Path | None:
    if registry:
        if arg:
            raise UsageError("check: use --fields or --registry, not both")
        return None
    chosen = arg or os.environ.get("LOGWARDEN_FIELDS") or cfg.fields
    if chosen:
        p = Path(chosen)
        if not p.is_file():
            raise UsageError(f"check: no such fields file: {chosen}")
        return p
    default = cwd / FIELDS_DEFAULT
    return default if default.is_file() else None


def resolve_fields(arg: str | None, registry: str | None, cwd: Path) -> Path | None:
    """--fields, then $LOGWARDEN_FIELDS, then config `fields`, then ./logwarden-fields.yaml."""
    return _fields(arg, registry, cwd, _load_config(cwd))


def resolve(cwd: Path, *, fields: str | None = None, registry: str | None = None,
            baseline: str | None = None, no_baseline: bool = False) -> tuple[Path | None, check.Policy]:
    """Fields file and policy (disabled rules, baseline) for a check run in `cwd`."""
    cfg = _load_config(cwd)
    policy = check.Policy(disabled=dict(cfg.disable))
    chosen = None if no_baseline else (baseline or cfg.baseline)
    if chosen:
        p = Path(chosen)
        if not p.is_file():
            raise UsageError(f"baseline {chosen} not found; create it with 'logwarden baseline <logs>'")
        try:
            policy.baseline = check.load_baseline(p)
        except check.CheckError as e:
            raise UsageError(str(e)) from e
        policy.baseline_path = os.path.relpath(p, cwd) if p.is_absolute() else str(p)
    return _fields(fields, registry, cwd, cfg), policy


def cmd_check(a: argparse.Namespace) -> int:
    text = _read_input(a.input)
    fields, policy = resolve(Path.cwd(), fields=a.fields, registry=a.registry,
                             baseline=a.baseline, no_baseline=a.no_baseline)
    try:
        result = check.run(text, fields=fields,
                           registry=Path(a.registry) if a.registry else None,
                           out=Path(a.o) if a.o else None, policy=policy)
    except check.CheckError as e:
        raise UsageError(str(e)) from e
    if a.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(output.text(result, a.max))
    if a.o:
        _err(f"full output in {a.o}")
    return 0 if result["ok"] else 1


def cmd_baseline(a: argparse.Namespace) -> int:
    cwd = Path.cwd()
    text = _read_input(a.input)
    fields, policy = resolve(cwd, fields=a.fields, registry=a.registry, no_baseline=True)
    cfg_path = config.load(cwd).baseline
    target = Path(a.output or cfg_path or cwd / config.DEFAULT_BASELINE)
    try:
        found, _, _ = check.evaluate(text, fields=fields,
                                     registry=Path(a.registry) if a.registry else None)
    except check.CheckError as e:
        raise UsageError(str(e)) from e
    content = check.make_baseline(found, policy)
    target.write_text(json.dumps(content, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    total = sum(e["count"] for e in content["entries"])
    print(f"wrote {target}: {len(content['entries'])} entries covering {total} findings")
    if not a.output and not cfg_path:
        print(f'next: add  baseline = "{config.DEFAULT_BASELINE}"  to [tool.logwarden] '
              "(or logwarden.toml) and commit both files")
    return 0


def cmd_rules(a: argparse.Namespace) -> int:
    rules = data.rules()
    if a.rule_id:
        r = rules.get(a.rule_id)
        if not r:
            raise UsageError(f"unknown rule '{a.rule_id}' (run: logwarden rules)")
        print(f"{a.rule_id} ({r['section']}) - {r['title']}\nfix: {r['hint']}\ndocs: {r['doc']}")
    else:
        for rid, r in rules.items():
            print(f"{rid}\t{r['section']}\t{r['title']}")
    return 0


def upsert_block(file: Path, snippet: str) -> str:
    block = f"{_BEGIN}\n{snippet.rstrip()}\n{_END}\n"
    if file.is_file():
        current = file.read_text(encoding="utf-8")
        if _BEGIN in current and _END in current:
            head, rest = current.split(_BEGIN, 1)
            tail = rest.split(_END, 1)[1].lstrip("\n")
            file.write_text(head + block + tail, encoding="utf-8")
            return f"updated logwarden section in {file}"
        sep = "" if not current or current.endswith("\n\n") else ("\n" if current.endswith("\n") else "\n\n")
        file.write_text(current + sep + block, encoding="utf-8")
        return f"added logwarden section to {file}"
    file.write_text(block, encoding="utf-8")
    return f"added logwarden section to {file}"


def cmd_init(a: argparse.Namespace) -> int:
    d = Path(a.dir)
    if not d.is_dir():
        raise UsageError(f"init: no such directory: {a.dir}")
    target = d / FIELDS_DEFAULT
    if target.exists():
        print(f"kept existing {target}")
    else:
        target.write_bytes(data.path(f"templates/{FIELDS_DEFAULT}").read_bytes())
        print(f"created {target}")
    if a.agent:
        snippet = data.path("templates/AGENTS.snippet.md").read_text(encoding="utf-8")
        print(upsert_block(d / "AGENTS.md", snippet))
        print("Claude Code: /plugin marketplace add Ingeniator/logwarden && /plugin install logwarden")
    print("next: capture JSON logs and run 'logwarden check <file>' "
          "(https://github.com/Ingeniator/logwarden/blob/main/docs/integration.md)")
    return 0


def cmd_setup(_: argparse.Namespace) -> int:
    try:
        print(weaver.find())
    except weaver.WeaverError as e:
        raise UsageError(str(e)) from e
    return 0


def cmd_mcp(_: argparse.Namespace) -> int:
    try:
        from logwarden import mcp_server
    except ImportError as e:
        raise UsageError("the MCP server needs the 'mcp' extra: "
                         "pip install 'logwarden[mcp]' (or uvx --from 'logwarden[mcp]' logwarden mcp)") from e
    mcp_server.main()
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="logwarden",
        description="Guardrail for structured logs. Exit codes: 0 no violations, "
                    "1 violations found, 2 usage or setup error.",
        epilog="Docs: https://github.com/Ingeniator/logwarden (docs/standard.md, docs/rules.md)")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", metavar="command")

    c = sub.add_parser("check", help="check JSON-lines logs (- reads stdin)")
    c.add_argument("input", help="log file, or - for stdin")
    c.add_argument("--json", action="store_true", help="machine-readable result (CI tools, AI agents)")
    c.add_argument("--fields", help=f"project-only fields file (default: config, then ./{FIELDS_DEFAULT})")
    c.add_argument("--registry", help="full Weaver registry overlay (advanced; replaces --fields)")
    c.add_argument("-o", metavar="DIR", help="keep samples.json, live_check.json and result.json in DIR")
    c.add_argument("--max", type=int, default=20, help="findings shown in text output (default 20)")
    c.add_argument("--baseline", metavar="FILE", help="fail only on findings not in FILE (default: config)")
    c.add_argument("--no-baseline", action="store_true", help="ignore the configured baseline")
    c.set_defaults(fn=cmd_check)

    b = sub.add_parser("baseline", help="record current findings so check fails only on new ones")
    b.add_argument("input", help="log file, or - for stdin")
    b.add_argument("--output", metavar="FILE",
                   help=f"baseline file (default: config 'baseline', then ./{config.DEFAULT_BASELINE})")
    b.add_argument("--fields", help="project-only fields file (as for check)")
    b.add_argument("--registry", help="full Weaver registry overlay (as for check)")
    b.set_defaults(fn=cmd_baseline)

    r = sub.add_parser("rules", help="list rules, or explain one")
    r.add_argument("rule_id", nargs="?")
    r.set_defaults(fn=cmd_rules)

    i = sub.add_parser("init", help="add logwarden files to a project")
    i.add_argument("dir", nargs="?", default=".")
    i.add_argument("--agent", action="store_true", help="also add a logwarden section to AGENTS.md")
    i.set_defaults(fn=cmd_init)

    s = sub.add_parser("setup", help="download weaver if missing and print its path")
    s.set_defaults(fn=cmd_setup)
    m = sub.add_parser("mcp", help="run the MCP server on stdio (needs logwarden[mcp])")
    m.set_defaults(fn=cmd_mcp)
    v = sub.add_parser("version", help="print the version")
    v.set_defaults(fn=lambda _: print(__version__) or 0)
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252; messages use §, ü, ...
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    p = parser()
    a = p.parse_args(argv)
    if not a.cmd:
        p.print_help()
        return 0
    try:
        return a.fn(a)
    except UsageError as e:
        _err(str(e))
        return 2
    except KeyboardInterrupt:
        return 130
