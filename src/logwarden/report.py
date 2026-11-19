"""Reports of a check result: a Markdown summary (PR comments, job summaries) and a
self-contained HTML page (CI artifact). Neither shows logged values: only rule messages,
which never contain them, event names and field names.
"""
from __future__ import annotations

import html as _html

from logwarden import __version__

MARKER = "<!-- logwarden-report -->"
COMMENT_LIMIT = 60_000      # GitHub's limit is 65,536 characters


def doc_url(doc: str | None) -> str | None:
    if not doc:
        return None
    ref = "main" if "dev" in __version__ else f"v{__version__}"
    return f"https://github.com/Ingeniator/logwarden/blob/{ref}/{doc}"


def _where(line) -> str:
    return "run" if line is None else str(line)


def _first(s) -> str:
    return str(s if s is not None else "").split("\n")[0]


# ---------------------------------------------------------------- Markdown

def _md(s) -> str:
    """Table-cell safe: no HTML, no pipes, no line breaks, no code-span breakouts."""
    s = _first(s)
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace("|", "\\|").replace("`", "'"))


def _code(s) -> str:
    return f"`{_md(s)}`" if s not in (None, "") else ""


def _md_report(result: dict, rows: int) -> str:
    out = [MARKER]
    n = result["violations"]
    out.append(f"### logwarden: {'✅ no violations' if n == 0 else f'❌ {n} violation' + ('s' if n != 1 else '')}")
    head = [f"**{result['records']}** records", f"**{n}** violations"]
    if result.get("baselined"):
        head.append(f"{result['baselined']} known in baseline")
    if result.get("skipped_lines"):
        head.append(f"{result['skipped_lines']} blank or non-JSON lines")
    out.append(" · ".join(head))

    cmp_ = result.get("compare")
    if cmp_:
        base = f" `{_md(cmp_['base'])}`" if cmp_.get("base") else ""
        out.append(f"\n**Compared with the base{base}:** **{cmp_['new']} new** · "
                   f"{cmp_['fixed']} fixed · {cmp_['unchanged']} unchanged")
        ev = []
        if cmp_.get("new_events"):
            ev.append("New events: " + ", ".join(_code(e) for e in cmp_["new_events"][:rows]))
        if cmp_.get("gone_events"):
            ev.append("No longer logged: " + ", ".join(_code(e) for e in cmp_["gone_events"][:rows]))
        if ev:
            out.append("\n" + " · ".join(ev))
        if cmp_["new_findings"]:
            out.append("\n#### New findings\n\n| Line | Rule | Event | Message |\n|---|---|---|---|")
            for f in cmp_["new_findings"][:rows]:
                out.append(f"| {_where(f['line'])} | {_code(f['rule'])} | {_code(f['event'])} | {_md(f['message'])} |")
            if cmp_["new"] > rows:
                out.append(f"\n… and {cmp_['new'] - rows} more new findings")
        if cmp_["fixed_entries"]:
            out.append("\n#### Fixed\n\n| Rule | Field | Event | Times in base |\n|---|---|---|---|")
            for e in cmp_["fixed_entries"][:rows]:
                out.append(f"| {_code(e['rule'])} | {_code(e['field'])} | {_code(e['event'])} | {e['count']} |")
            if cmp_["fixed"] > rows:
                out.append(f"\n… and {cmp_['fixed'] - rows} more fixed")

    if result["by_rule"]:
        out.append("\n#### Violations by rule\n\n| Count | Rule | Fix |\n|---|---|---|")
        for r in result["by_rule"][:rows]:
            url = doc_url(r.get("doc"))
            rule = f"[{_code(r['rule'])}]({url})" if url else _code(r["rule"])
            title = f" {_md(r['title'])}" if r.get("title") else ""
            out.append(f"| {r['count']} | {rule}{title} | {_md(r.get('hint') or '')} |")
        if len(result["by_rule"]) > rows:
            out.append(f"\n… and {len(result['by_rule']) - rows} more rules")
    if result["findings"]:
        shown = result["findings"][:rows]
        out.append(f"\n<details><summary>Findings (first {len(shown)} of {n})</summary>\n\n"
                   "| Line | Rule | Message |\n|---|---|---|")
        for f in shown:
            out.append(f"| {_where(f['line'])} | {_code(f['rule'])} | {_md(f['message'])} |")
        out.append("\n</details>")
    extra = []
    if result.get("advice"):
        extra.append("Not failing: " + ", ".join(f"{_code(a['rule'])} {a['count']}" for a in result["advice"]))
    for d in result.get("disabled") or []:
        extra.append(f"Disabled: {_code(d['rule'])} ({d['count']} not counted): {_md(d['reason'])}")
    if result.get("baseline_fixed"):
        extra.append(f"Baseline: {result['baseline_fixed']} entries no longer occur; "
                     "run `logwarden baseline` to shrink it")
    if extra:
        out.append("\n" + "<br>\n".join(extra))
    out.append(f"\n<sub>logwarden {_md(result.get('version', __version__))}</sub>")
    return "\n".join(out) + "\n"


def markdown(result: dict, limit: int = COMMENT_LIMIT, rows: int = 20) -> str:
    """Markdown summary that fits in `limit` characters (a GitHub comment)."""
    while True:
        md = _md_report(result, rows)
        if len(md) <= limit:
            return md
        if rows <= 1:
            cut = "\n\n… report cut to fit; see the full HTML report\n"
            return md[:limit - len(cut)] + cut
        rows //= 2


# ---------------------------------------------------------------- HTML

def _h(s) -> str:
    return _html.escape("" if s is None else str(s), quote=True)


_CSS = """
:root{--bg:#fbfaf7;--fg:#1d1c1a;--muted:#6b675f;--line:#e4e0d8;--card:#ffffff;
--bad:#b42318;--bad-bg:#fdecea;--ok:#1e7a46;--ok-bg:#e6f4ea;--new:#9a4b00;--new-bg:#fff1e0;--code:#f2efe9}
@media (prefers-color-scheme:dark){:root{--bg:#161513;--fg:#ece9e3;--muted:#a19c92;--line:#34312c;
--card:#1e1d1a;--bad:#ff8a7a;--bad-bg:#3a1d19;--ok:#7ad69c;--ok-bg:#16301f;--new:#ffb366;--new-bg:#3a2a14;--code:#2a2824}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1100px;margin:0 auto;padding:32px 16px 64px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:36px 0 10px;padding-bottom:6px;border-bottom:1px solid var(--line)}
.meta{color:var(--muted);font-size:13px}
.pill{display:inline-block;padding:2px 10px;border-radius:999px;font-weight:600;font-size:13px;vertical-align:middle}
.pill.ok{background:var(--ok-bg);color:var(--ok)}.pill.bad{background:var(--bad-bg);color:var(--bad)}
.stats{display:flex;flex-wrap:wrap;gap:12px;margin:20px 0 0}
.stat{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 16px;min-width:120px}
.stat b{display:block;font-size:22px;font-variant-numeric:tabular-nums}.stat span{color:var(--muted);font-size:13px}
.stat.new b{color:var(--new)}.stat.bad b{color:var(--bad)}.stat.ok b{color:var(--ok)}
code{font:13px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:var(--code);padding:1px 5px;border-radius:4px;overflow-wrap:break-word}
td code{white-space:nowrap}td:last-child code{white-space:normal}td:last-child{min-width:240px}
.scroll{overflow-x:auto;border:1px solid var(--line);border-radius:8px;background:var(--card)}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-weight:600;color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.03em}
tr:last-child td{border-bottom:0}td.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.rule{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 16px;margin:0 0 10px}
.rule h3{font-size:15px;margin:0 0 4px}.rule p{margin:4px 0}.rule .count{float:right;color:var(--bad);font-weight:600}
.muted{color:var(--muted)}a{color:inherit}
"""


def _table(head: list[str], rows: list[list[str]], numeric: set[int] = frozenset()) -> str:
    th = "".join(f"<th>{h}</th>" for h in head)
    body = "".join("<tr>" + "".join(f'<td class="n">{c}</td>' if i in numeric else f"<td>{c}</td>"
                                    for i, c in enumerate(r)) + "</tr>" for r in rows)
    return f'<div class="scroll"><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'


def _c(s) -> str:
    return f"<code>{_h(s)}</code>" if s not in (None, "") else ""


def html(result: dict, events: dict | None = None, title: str = "logwarden report") -> str:
    """One self-contained page: no scripts, no external styles or fonts."""
    n = result["violations"]
    parts = [f"<h1>{_h(title)} <span class=\"pill {'ok' if n == 0 else 'bad'}\">"
             f"{'no violations' if n == 0 else f'{n} violation' + ('s' if n != 1 else '')}</span></h1>",
             f'<div class="meta">logwarden {_h(result.get("version"))}</div>']
    stats = [("records", result["records"], ""), ("violations", n, "bad" if n else "ok")]
    if result.get("baselined"):
        stats.append(("known in baseline", result["baselined"], ""))
    if result.get("skipped_lines"):
        stats.append(("blank or non-JSON lines", result["skipped_lines"], ""))
    cmp_ = result.get("compare")
    if cmp_:
        stats += [("new vs base", cmp_["new"], "new" if cmp_["new"] else "ok"),
                  ("fixed vs base", cmp_["fixed"], "ok" if cmp_["fixed"] else "")]
    parts.append('<div class="stats">' + "".join(
        f'<div class="stat {cls}"><b>{v}</b><span>{_h(label)}</span></div>' for label, v, cls in stats) + "</div>")

    if cmp_:
        base = f" {_c(cmp_['base'])}" if cmp_.get("base") else ""
        parts.append(f"<h2>Compared with the base{base}</h2>")
        parts.append(f"<p>{cmp_['new']} new · {cmp_['fixed']} fixed · {cmp_['unchanged']} unchanged</p>")
        if cmp_.get("new_events"):
            parts.append("<p>New events: " + ", ".join(_c(e) for e in cmp_["new_events"]) + "</p>")
        if cmp_.get("gone_events"):
            parts.append("<p>No longer logged: " + ", ".join(_c(e) for e in cmp_["gone_events"]) + "</p>")
        if cmp_["new_findings"]:
            parts.append("<h3>New findings</h3>" + _table(
                ["Line", "Rule", "Event", "Message"],
                [[_h(_where(f["line"])), _c(f["rule"]), _c(f["event"]), _h(f["message"])]
                 for f in cmp_["new_findings"]], {0}))
        if cmp_["fixed_entries"]:
            parts.append("<h3>Fixed</h3>" + _table(
                ["Rule", "Field", "Event", "Times in base"],
                [[_c(e["rule"]), _c(e["field"]), _c(e["event"]), _h(e["count"])]
                 for e in cmp_["fixed_entries"]], {3}))

    if result["by_rule"]:
        parts.append("<h2>Violations by rule</h2>")
        for r in result["by_rule"]:
            url = doc_url(r.get("doc"))
            link = f' · <a href="{_h(url)}">docs</a>' if url else ""
            parts.append(
                f'<div class="rule"><span class="count">{r["count"]}</span>'
                f"<h3>{_c(r['rule'])} {_h(r.get('title') or '')}</h3>"
                f"<p><b>Fix:</b> {_h(r.get('hint') or '')}{link}</p>"
                f'<p class="muted">e.g. {_h(_first(r["example"]))}</p></div>')
    if result["findings"]:
        more = (f" (first {len(result['findings'])} of {n})" if result.get("findings_truncated") else "")
        parts.append(f"<h2>Findings{more}</h2>" + _table(
            ["Line", "Rule", "Field", "Event", "Message"],
            [[_h(_where(f["line"])), _c(f["rule"]), _c(f["field"]), _c(f["event"]), _h(f["message"])]
             for f in result["findings"]], {0}))
    if result.get("advice") or result.get("disabled"):
        parts.append("<h2>Not failing</h2><ul>"
                     + "".join(f"<li>{_c(a['rule'])} ({_h(a['level'])}): {a['count']}</li>"
                               for a in result.get("advice") or [])
                     + "".join(f"<li>{_c(d['rule'])} disabled, {d['count']} not counted: {_h(d['reason'])}</li>"
                               for d in result.get("disabled") or [])
                     + "</ul>")
    if events:
        parts.append(f"<h2>Events in this run ({len(events)})</h2>" + _table(
            ["Event", "Count", "Levels", "First line", "Fields"],
            [[_c(name), _h(e["count"]), _h(", ".join(e["levels"])), _h(e["first_line"]),
              " ".join(_c(f) for f in e["fields"])] for name, e in events.items()], {1, 3}))
    return ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>{_h(title)}</title><style>{_CSS}</style></head>"
            f"<body><main>{''.join(parts)}</main></body></html>\n")
