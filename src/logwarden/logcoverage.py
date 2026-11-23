"""Which log calls do the tests run? (`logwarden log-coverage`, v0.11.0)

Finds the log calls in Python source with `ast` and matches them with coverage data
(coverage.py's `.coverage` SQLite file, `coverage json`, Cobertura XML) and, optionally,
with captured log lines (`code.file.path` + `code.line.number`). Standard library only.
"""
from __future__ import annotations

import ast
import fnmatch
import json
import re
import sqlite3
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path, PurePath

from logwarden import __version__

LEVELS = ("debug", "info", "warning", "warn", "error", "exception", "critical", "fatal", "msg", "log")
# The level a call logs at, for counting: warn -> warning, fatal -> critical, msg -> info.
_LEVEL_OF = {"warn": "warning", "fatal": "critical", "msg": "info"}
COUNTED = ("debug", "info", "warning", "error", "exception", "critical")
# A receiver with log, logger, logging, structlog or slog as a word: log, self._logger,
# app.logger, structlog.get_logger().bind(x=1). Not dialog, catalog, self.
RECEIVER = r"(?i)(?<![a-z0-9])_*(log|logger|logging|structlog|slog)(?![a-z0-9])"
SKIP_DIRS = {".venv", "venv", "node_modules", "tests", "test", "build", "dist", "migrations",
             ".git", "__pycache__", ".tox", ".nox", "site-packages"}
_PRAGMA = re.compile(r"#\s*(pragma|PRAGMA)[:\s]?\s*(no|NO)\s*(cover|COVER)")


class LogCoverageError(Exception):
    """Bad input: no such file, unreadable coverage data. CLI exit code 2."""


@dataclass(frozen=True)
class Call:
    path: str          # as shown: relative to the current directory when inside it, POSIX
    file: Path         # resolved
    line: int          # first line of the call
    end_line: int
    stmt_line: int     # first line of the innermost statement containing the call
    level: str
    receiver: str


# ---------------------------------------------------------------- source

def _shown(p: Path, cwd: Path) -> str:
    try:
        return p.relative_to(cwd).as_posix()
    except ValueError:
        return p.as_posix()


def _files(paths: list[str], excludes: list[str], cwd: Path):
    for raw in paths or ["."]:
        p = Path(raw) if Path(raw).is_absolute() else cwd / raw
        if not p.exists():
            raise LogCoverageError(f"no such file or directory: {raw}")
        found = [p] if p.is_file() else sorted(p.rglob("*.py"))
        for f in found:
            rel = f.relative_to(p) if p.is_dir() else PurePath(f.name)
            if p.is_dir() and (SKIP_DIRS & set(rel.parts[:-1]) or f.name.startswith("test_")
                               or f.name.endswith("_test.py") or f.name == "conftest.py"):
                continue
            shown = _shown(f.resolve(), cwd)
            if any(fnmatch.fnmatch(shown, g) or fnmatch.fnmatch(f.as_posix(), g) for g in excludes):
                continue
            yield f


def _excluded_lines(text: str, tree: ast.AST) -> set[int]:
    """Lines coverage.py excludes by default: a `# pragma: no cover` line, and the whole body of
    a statement whose first line has it."""
    marked = {i for i, line in enumerate(text.splitlines(), 1) if _PRAGMA.search(line)}
    out = set(marked)
    for node in ast.walk(tree):
        if isinstance(node, ast.stmt) and node.lineno in marked:
            out.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    return out


def find_calls(paths: list[str], *, receiver: str = RECEIVER, excludes: list[str] | None = None,
               cwd: Path | None = None) -> tuple[list[Call], int]:
    """Log calls in the Python files under `paths`, and how many were skipped by
    `# pragma: no cover`."""
    cwd = (cwd or Path.cwd()).resolve()
    rx = re.compile(receiver)
    calls: list[Call] = []
    pragma = 0
    for f in _files(paths, excludes or [], cwd):
        try:
            text = f.read_text(encoding="utf-8")
            tree = ast.parse(text, filename=str(f))
        except (SyntaxError, UnicodeDecodeError, ValueError):
            continue
        excluded = _excluded_lines(text, tree)
        inner: dict[int, ast.stmt] = {}
        for node in ast.walk(tree):            # outer statements first: inner ones overwrite
            if isinstance(node, ast.stmt):
                for child in ast.walk(node):
                    if isinstance(child, ast.Call):
                        inner[id(child)] = node
        resolved = f.resolve()
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in LEVELS):
                continue
            recv = ast.unparse(node.func.value)
            if not rx.search(recv):
                continue
            level = node.func.attr
            if level == "log":                 # logger.log(logging.ERROR, ...) / log(40, ...)
                level = _level_arg(node)
                if level is None:
                    continue
            level = _LEVEL_OF.get(level, level)
            stmt = inner.get(id(node))
            stmt_line = stmt.lineno if stmt else node.lineno
            if node.lineno in excluded or stmt_line in excluded:
                pragma += 1
                continue
            calls.append(Call(_shown(resolved, cwd), resolved, node.lineno,
                              node.end_lineno or node.lineno, stmt_line, level, recv))
    calls.sort(key=lambda c: (c.path, c.line))
    return calls, pragma


_NUMERIC = {10: "debug", 20: "info", 30: "warning", 40: "error", 50: "critical"}


def _level_arg(node: ast.Call) -> str | None:
    if not node.args:
        return None
    a = node.args[0]
    if isinstance(a, ast.Attribute) and a.attr.lower() in LEVELS:
        return a.attr.lower()
    if isinstance(a, ast.Name) and a.id.lower() in LEVELS:
        return a.id.lower()
    if isinstance(a, ast.Constant) and isinstance(a.value, int):
        return _NUMERIC.get(a.value)
    if isinstance(a, ast.Constant) and isinstance(a.value, str) and a.value.lower() in LEVELS:
        return a.value.lower()
    return "info"                              # a level we can't read: count it, as info


# ---------------------------------------------------------------- coverage data

def read_coverage(path: Path) -> dict[str, set[int]]:
    """File path (as recorded) -> executed lines, from any of the three formats."""
    if not path.is_file():
        raise LogCoverageError(f"no such coverage file: {path}")
    head = path.read_bytes()[:64]
    try:
        if head.startswith(b"SQLite format 3"):
            return _from_sqlite(path)
        text = path.read_text(encoding="utf-8")
        if text.lstrip().startswith("{"):
            return _from_json(json.loads(text), path)
        if text.lstrip().startswith("<"):
            return _from_xml(ET.fromstring(text), path)
    except (ValueError, ET.ParseError, sqlite3.Error, UnicodeDecodeError, KeyError) as e:
        raise LogCoverageError(f"can't read coverage data {path}: {e}") from e
    raise LogCoverageError(f"{path} is not coverage data (.coverage, coverage json or Cobertura XML)")


def _from_json(d: dict, path: Path) -> dict[str, set[int]]:
    if "files" not in d:
        raise LogCoverageError(f"{path}: no 'files' (not a `coverage json` report)")
    return {name: set(f.get("executed_lines") or []) for name, f in d["files"].items()}


def _from_xml(root: ET.Element, path: Path) -> dict[str, set[int]]:
    if root.tag != "coverage":
        raise LogCoverageError(f"{path}: not a Cobertura report")
    sources = [s.text.strip() for s in root.iter("source") if s.text and s.text.strip()]
    out: dict[str, set[int]] = {}
    for cls in root.iter("class"):
        name = cls.get("filename")
        if not name:
            continue
        lines = {int(ln.get("number")) for ln in cls.iter("line") if int(ln.get("hits", "0")) > 0}
        full = next((str(Path(s) / name) for s in sources if (Path(s) / name).is_file()),
                    str(Path(sources[0]) / name) if sources else name)
        out.setdefault(full, set()).update(lines)
    return out


def _numbits(b: bytes) -> set[int]:
    return {i * 8 + j for i, byte in enumerate(b) for j in range(8) if byte & (1 << j)}


def _from_sqlite(path: Path) -> dict[str, set[int]]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        files = dict(con.execute("select id, path from file"))
        out: dict[str, set[int]] = {p: set() for p in files.values()}
        tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
        if "line_bits" in tables:
            for fid, bits in con.execute("select file_id, numbits from line_bits"):
                out[files[fid]] |= _numbits(bits)
        if "arc" in tables:                    # branch coverage: lines are the arcs' ends
            for fid, a, b in con.execute("select file_id, fromno, tono from arc"):
                out[files[fid]].update(n for n in (a, b) if n > 0)
        return out
    finally:
        con.close()


def _parts(p: str) -> tuple[str, ...]:
    return tuple(x for x in re.split(r"[\\/]+", p) if x and x != ".")


class _Matcher:
    """Finds the recorded path for a source file: the same file, else the recorded path whose
    trailing parts match the most (CI runs in another directory, relative_files, Windows)."""

    def __init__(self, recorded: dict[str, set[int]], base: Path):
        self.by_name: dict[str, list[tuple[tuple[str, ...], str]]] = {}
        self.exact: dict[str, str] = {}
        for name in recorded:
            p = Path(name) if Path(name).is_absolute() else base / name
            try:
                self.exact[str(p.resolve())] = name
            except OSError:
                pass
            parts = _parts(name)
            if parts:
                self.by_name.setdefault(parts[-1], []).append((parts, name))

    def find(self, f: Path) -> str | None:
        if (hit := self.exact.get(str(f))) is not None:
            return hit
        mine = _parts(f.as_posix())
        best, score = None, 0
        for parts, name in self.by_name.get(mine[-1], []):
            n = 0
            while n < min(len(parts), len(mine)) and parts[-1 - n] == mine[-1 - n]:
                n += 1
            if n > score:
                best, score = name, n
        return best


def read_logs(path: Path) -> dict[str, set[int]]:
    """code.file.path -> code.line.number of each captured line (JSON lines)."""
    if not path.is_file():
        raise LogCoverageError(f"no such log file: {path}")
    out: dict[str, set[int]] = {}
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            attrs = obj.get("attributes") if isinstance(obj.get("attributes"), dict) else {}
            p = obj.get("code.file.path", attrs.get("code.file.path"))
            n = obj.get("code.line.number", attrs.get("code.line.number"))
            if isinstance(p, str) and isinstance(n, int) and not isinstance(n, bool):
                out.setdefault(p, set()).add(n)
    return out


# ---------------------------------------------------------------- result

def measure(paths: list[str], *, coverage: Path | None = None, logs: Path | None = None,
            levels: list[str] | None = None, receiver: str = RECEIVER,
            excludes: list[str] | None = None, fail_under: float | None = None,
            cwd: Path | None = None) -> dict:
    if coverage is None and logs is None:
        raise LogCoverageError("give --coverage FILE (coverage.py data), --logs FILE (captured lines), or both")
    unknown = sorted(set(levels or []) - set(COUNTED))
    if unknown:
        raise LogCoverageError(f"unknown level {', '.join(unknown)}; levels: {', '.join(COUNTED)}")
    try:
        re.compile(receiver)
    except re.error as e:
        raise LogCoverageError(f"bad --receiver: {e}") from e
    cwd = (cwd or Path.cwd()).resolve()
    calls, pragma = find_calls(paths, receiver=receiver, excludes=excludes, cwd=cwd)
    if levels:
        calls = [c for c in calls if c.level in levels]

    reached = emitted = None
    if coverage is not None:
        data = read_coverage(coverage)
        m = _Matcher(data, coverage.resolve().parent)
        reached = set()
        for c in calls:
            name = m.find(c.file)
            if name is not None and c.stmt_line in data[name]:
                reached.add(c)
    if logs is not None:
        data = read_logs(logs)
        m = _Matcher(data, cwd)
        emitted = set()
        for c in calls:
            name = m.find(c.file)
            if name is not None and any(c.line <= n <= c.end_line for n in data[name]):
                emitted.add(c)
    measured = emitted if emitted is not None else reached
    key = "emitted" if emitted is not None else "reached"

    def count(group: list[Call]) -> dict:
        d = {"calls": len(group)}
        if reached is not None:
            d["reached"] = sum(c in reached for c in group)
        if emitted is not None:
            d["emitted"] = sum(c in emitted for c in group)
        return d

    by_level = {lv: count([c for c in calls if c.level == lv]) for lv in COUNTED
                if any(c.level == lv for c in calls)}
    files = [{"path": p, **count([c for c in calls if c.path == p])}
             for p in sorted({c.path for c in calls})]
    files.sort(key=lambda f: (-(f["calls"] - f[key]), f["path"]))
    total = len(calls)
    pct = round(100.0 * len(measured) / total, 1) if total else 100.0
    item = lambda c: {"path": c.path, "line": c.line, "level": c.level, "receiver": c.receiver}
    return {
        "tool": "logwarden",
        "version": __version__,
        "ok": fail_under is None or pct >= fail_under,
        "measure": key,
        "calls": total,
        "reached": None if reached is None else len(reached),
        "emitted": None if emitted is None else len(emitted),
        "percent": pct,
        "fail_under": fail_under,
        "levels": list(levels) if levels else None,
        "excluded_by_pragma": pragma,
        "by_level": by_level,
        "files": files,
        "unreached": None if reached is None else [item(c) for c in calls if c not in reached],
        "not_emitted": None if emitted is None else [item(c) for c in calls if c not in emitted],
    }


def _ratio(d: dict, key: str) -> str:
    n = d["calls"]
    return f"{d[key]}/{n}" + (f" ({100 * d[key] / n:.0f}%)" if n else "")


def text(r: dict, limit: int = 50) -> str:
    key = r["measure"]
    out = []
    what = f" ({', '.join(r['levels'])})" if r["levels"] else ""
    head = []
    if r["reached"] is not None:
        head.append(f"{r['reached']} of {r['calls']} log calls{what} reached by the tests")
    if r["emitted"] is not None:
        head.append(f"{r['emitted']} of {r['calls']} log calls{what} wrote a line")
    out.append("log-coverage: " + "; ".join(head) + f" ({r['percent']:g}% {key})")
    if r["by_level"]:
        out.append("  by level: " + ", ".join(f"{lv} {_ratio(d, key)}" for lv, d in r["by_level"].items()))
    worst = [f for f in r["files"] if f["calls"] - f[key]][:5]
    if worst:
        out.append("  most " + ("unreached" if key == "reached" else "not emitted") + ": "
                   + "; ".join(f"{f['path']} {f['calls'] - f[key]}/{f['calls']}" for f in worst))
    if r["excluded_by_pragma"]:
        out.append(f"  not counted: {r['excluded_by_pragma']} under '# pragma: no cover'")
    missing = r["unreached"] if key == "reached" else r["not_emitted"]
    if missing:
        label = "not reached" if key == "reached" else "wrote no line"
        out.append(f"\n{label} (first {min(limit, len(missing))} of {len(missing)}):")
        out.extend(f"  {m['path']}:{m['line']}  {m['level']}  {m['receiver']}" for m in missing[:limit])
    if r["fail_under"] is not None:
        verdict = "ok" if r["ok"] else "FAIL"
        out.append(f"\n{verdict}: {r['percent']:g}% {key}, required {r['fail_under']:g}%")
    return "\n".join(out)


def markdown(r: dict, limit: int = 50) -> str:
    key = r["measure"]
    what = f" ({', '.join(r['levels'])})" if r["levels"] else ""
    out = [f"### Log coverage: {r['percent']:g}% of {r['calls']} log calls{what} {key}", ""]
    cols = ["Level", "Calls"] + (["Reached"] if r["reached"] is not None else []) + \
        (["Wrote a line"] if r["emitted"] is not None else [])
    out += ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for lv, d in r["by_level"].items():
        row = [lv, str(d["calls"])] + ([str(d["reached"])] if "reached" in d else []) + \
            ([str(d["emitted"])] if "emitted" in d else [])
        out.append("| " + " | ".join(row) + " |")
    missing = r["unreached"] if key == "reached" else r["not_emitted"]
    if missing:
        out += ["", "<details><summary>" + ("Not reached" if key == "reached" else "Wrote no line")
                + f" ({len(missing)})</summary>", ""]
        out += [f"- `{m['path']}:{m['line']}` {m['level']} (`{m['receiver']}`)" for m in missing[:limit]]
        if len(missing) > limit:
            out.append(f"- … {len(missing) - limit} more (`--json` for all)")
        out += ["", "</details>"]
    if r["fail_under"] is not None:
        out += ["", ("✅" if r["ok"] else "❌") + f" required {r['fail_under']:g}%"]
    out.append(f"\n<sub>logwarden {__version__}</sub>")
    return "\n".join(out) + "\n"
