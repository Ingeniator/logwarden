"""logwarden command line. Exit codes: 0 clean, 1 violations, 2 usage or setup error."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from logwarden import __version__, check, config, data, output, selflog, weaver

FIELDS_DEFAULT = config.DEFAULT_FIELDS
_BEGIN, _END = "<!-- logwarden:begin -->", "<!-- logwarden:end -->"


class UsageError(Exception):
    pass


def _err(msg: str, event: str = "command_failed", level: str = "error",
         error_type: str | None = "usage_error") -> None:
    """A message on stderr (plain text, or a JSON record with LOGWARDEN_LOG_FORMAT=json)."""
    selflog.emit(event, msg, level, error_type=error_type if level in ("warning", "error") else None)


def _info(msg: str, event: str) -> None:
    selflog.emit(event, msg)


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
        _err(f"warning: {w}", "config_warning", "warning", "invalid_config")
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


def source_of(cfg: config.Config, a: argparse.Namespace | None = None) -> "sources.Source":
    """[source] from the config, overridden by --format, --message-field, --ignore-field, --service."""
    from logwarden import sources
    src = sources.Source(format=cfg.input.get("format", "auto"),
                         message_field=cfg.input.get("message_field"),
                         ignore_fields=list(cfg.input.get("ignore_fields", [])),
                         service_field=cfg.input.get("service_field", "service.name"))
    if a is not None:
        if getattr(a, "format", None):
            src.format = a.format
        if getattr(a, "message_field", None):
            src.message_field = a.message_field
        src.ignore_fields += getattr(a, "ignore_field", None) or []
        if getattr(a, "service_field", None):
            src.service_field = a.service_field
        src.services = getattr(a, "service", None) or None
    return src


def resolve(cwd: Path, *, fields: str | None = None, registry: str | None = None,
            baseline: str | None = None, no_baseline: bool = False,
            args: argparse.Namespace | None = None) -> tuple[Path | None, check.Policy]:
    """Fields file and policy (disabled rules, baseline, input format) for a check run in `cwd`."""
    cfg = _load_config(cwd)
    policy = check.Policy(disabled=dict(cfg.disable), upstreams=list(cfg.upstreams),
                          source=source_of(cfg, args),
                          profile=getattr(args, "profile", None) or cfg.profile or "standard")
    if cfg.events:
        from logwarden import catalog
        p = Path(cfg.events)
        if not p.is_file():
            raise UsageError(f"event catalog {p} not found; create it with "
                             f"'logwarden catalog <logs> -o {os.path.relpath(p, cwd)}'")
        try:
            policy.events = catalog.load(p)
        except catalog.CatalogError as e:
            raise UsageError(str(e)) from e
        policy.events_path = os.path.relpath(p, cwd)
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
    if a.signal == "metrics":
        return _check_metrics(_read_input(a.input), a)
    return _check_text(_read_input(a.input), a)


_LOG_ONLY = ("baseline", "compare", "snapshot", "html", "sarif", "codequality", "metrics", "o")


def _check_metrics(text: str, a: argparse.Namespace) -> int:
    """check --signal metrics (experimental): Prometheus / OpenMetrics text."""
    from logwarden import metrics
    used = [f"--{n.replace('_', '-')}" if n != "o" else "-o" for n in _LOG_ONLY if getattr(a, n, None)]
    if used:
        raise UsageError(f"check --signal metrics: {', '.join(used)} not available for metrics yet "
                         "(planned for v0.13.0)")
    cwd = Path.cwd()
    cfg = _load_config(cwd)
    fields = resolve_fields(a.fields, a.registry, cwd)
    try:
        result = metrics.run(text, fields=fields, registry=Path(a.registry) if a.registry else None,
                             series_budget=a.series_budget, disabled=set(cfg.disable or {}))
    except check.CheckError as e:
        raise UsageError(str(e)) from e
    if a.markdown:
        _write(Path(a.markdown), metrics.markdown(result, a.max))
    print(json.dumps(result, indent=2, ensure_ascii=False) if a.json else metrics.text(result, a.max))
    return 0 if result["ok"] else 1


def _write(path: Path, content: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as e:
        raise UsageError(f"can't write {path}: {e}") from e


def _check_text(text: str, a: argparse.Namespace) -> int:
    from logwarden import report
    fields, policy = resolve(Path.cwd(), fields=a.fields, registry=a.registry,
                             baseline=a.baseline, no_baseline=a.no_baseline, args=a)
    base = None
    if a.compare:
        p = Path(a.compare)
        if not p.is_file():
            raise UsageError(f"--compare: no such file: {a.compare}")
        try:
            base = check.load_compare(p)
        except check.CheckError as e:
            raise UsageError(f"--compare: {e}") from e
    try:
        an = check.analyse(text, fields=fields,
                           registry=Path(a.registry) if a.registry else None,
                           out=Path(a.o) if a.o else None, policy=policy,
                           compare=base, compare_path=a.compare)
    except check.CheckError as e:
        raise UsageError(str(e)) from e
    result = an.result
    if a.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(output.text(result, a.max))
    snapshot = json.dumps(an.snapshot, indent=2, ensure_ascii=False) + "\n"

    def sarif_doc() -> str:
        from logwarden import sarif
        log_path = getattr(a, "input", None) or getattr(a, "output", None)   # capture: its output
        log_path = None if log_path == "-" else log_path
        return json.dumps(sarif.from_check(an.violations, log_path, a.source_root), indent=2,
                          ensure_ascii=False) + "\n"

    def codequality_doc() -> str:
        from logwarden import codequality
        log_path = getattr(a, "input", None) or getattr(a, "output", None)
        log_path = None if log_path == "-" else log_path
        return codequality.dumps(codequality.from_check(an.violations, log_path, a.source_root))

    targets = [(a.snapshot, lambda: snapshot), (a.markdown, lambda: report.markdown(result)),
               (a.codequality, codequality_doc),
               (a.html, lambda: report.html(result, an.events)),
               (a.sarif, sarif_doc), (a.metrics, lambda: report.metrics(result))]
    if a.o:
        targets += [(str(Path(a.o) / "snapshot.json"), lambda: snapshot),
                    (str(Path(a.o) / "report.md"), lambda: report.markdown(result)),
                    (str(Path(a.o) / "report.html"), lambda: report.html(result, an.events))]
    for path, render in targets:
        if path:
            _write(Path(path), render())
    if getattr(a, "pushgateway", None):
        from logwarden import fetch
        try:
            _info(f"pushed metrics to {fetch.push(a.pushgateway, report.metrics(result))}", "metrics_pushed")
        except fetch.FetchError as e:
            raise UsageError(str(e)) from e
    if a.o:
        _info(f"full output in {a.o}", "output_written")
    return 0 if result["ok"] else 1


def _types_for_catalog() -> dict[str, str]:
    """CSV value types only matter for fields; the catalog lists events, so strings are fine."""
    return {}


def cmd_catalog(a: argparse.Namespace) -> int:
    from logwarden import catalog
    try:
        records = check.read(_read_input(a.input), source_of(_load_config(Path.cwd()), a),
                             _types_for_catalog).records
    except check.CheckError as e:
        raise UsageError(str(e)) from e
    seen = catalog.collect(records)
    if a.output:
        target = Path(a.output)
        try:
            added, total = catalog.write(target, seen)
        except catalog.CatalogError as e:
            raise UsageError(str(e)) from e
        except OSError as e:
            raise UsageError(f"can't write {target}: {e}") from e
        print(f"wrote {target}: {total} events, {added} new"
              + ("; describe them, then set  events = \"" + a.output + "\"  in the logwarden config"
                 if added else ""))
        return 0
    if a.json:
        print(json.dumps({"tool": "logwarden", "version": __version__, "events": seen},
                         indent=2, ensure_ascii=False))
    elif a.markdown:
        described = {}
        cfg = config.load(Path.cwd())
        if cfg.events and Path(cfg.events).is_file():
            try:
                described = catalog.load(Path(cfg.events))
            except catalog.CatalogError:
                pass
        print(catalog.markdown(seen, described), end="")
    else:
        print(catalog.text(seen))
    return 0


_OTEL_PROTOCOLS = ("http/protobuf", "http/json")


def _otel_env(endpoint: str) -> dict[str, str]:
    env = dict(os.environ)
    env["OTEL_EXPORTER_OTLP_ENDPOINT"] = endpoint
    env["OTEL_EXPORTER_OTLP_LOGS_ENDPOINT"] = endpoint + "/v1/logs"
    env["OTEL_LOGS_EXPORTER"] = "otlp"
    for k in ("OTEL_EXPORTER_OTLP_PROTOCOL", "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL"):
        if env.get(k) not in _OTEL_PROTOCOLS:   # gRPC isn't supported by the receiver
            env[k] = "http/protobuf"
    return env


def _wait_for_signal() -> None:
    import signal
    import threading
    stop = threading.Event()
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), lambda *_: stop.set())
    while not stop.wait(0.5):
        pass


def cmd_capture(a: argparse.Namespace) -> int:
    import shutil
    import subprocess
    import time

    from logwarden import capture
    command = a.command[1:] if a.command[:1] == ["--"] else a.command
    port = a.port if a.port is not None else (0 if command else 4318)
    out = Path(a.output)
    try:
        rx = capture.Receiver(out, host=a.host, port=port, log=lambda m: _err(
            m, "capture_request_rejected", "warning", "invalid_otlp_request")).start()
    except OSError as e:
        raise UsageError(f"capture: can't listen on {a.host}:{port}: {e}") from e
    rc = 0
    try:
        if command:
            exe = shutil.which(command[0])
            if not exe:
                raise UsageError(f"capture: command not found: {command[0]}")
            _info(f"receiving OTLP on {rx.endpoint}; running: {' '.join(command)}", "capture_started")
            # The command's stdout goes to stderr: our stdout is only the result (--json stays parseable).
            sys.stdout.flush()
            rc = subprocess.run([exe, *command[1:]], env=_otel_env(rx.endpoint),
                                stdout=sys.stderr.fileno()).returncode
            time.sleep(0.3)  # let in-flight exports land
        else:
            _info(f"receiving OTLP/HTTP logs on {rx.endpoint}/v1/logs into {out} (Ctrl-C to stop)",
                  "capture_started")
            _wait_for_signal()
    finally:
        rx.stop()
    _info(f"captured {rx.count} log records into {out}", "capture_finished")
    if rc:
        _err(f"capture: command exited with {rc}", "capture_command_failed", "error", "nonzero_exit")
        return rc
    if a.no_check:
        return 0
    if rx.count == 0:
        raise UsageError("capture: no log records arrived. Enable the OpenTelemetry SDK's logs "
                         "exporter (OTEL_LOGS_EXPORTER=otlp) with protocol http/protobuf or http/json")
    return _check_text(out.read_text(encoding="utf-8"), a)


def cmd_fetch(a: argparse.Namespace) -> int:
    from logwarden import fetch
    try:
        raw, got, matched = fetch.es(a.es, a.index, since=a.since, size=a.size, time_field=a.time_field,
                                     query_string=a.query, auth=os.environ.get("LOGWARDEN_ES_AUTH"),
                                     ca=a.ca or os.environ.get("LOGWARDEN_ES_CA"), insecure=a.insecure)
    except fetch.FetchError as e:
        raise UsageError(f"fetch: {e}") from e
    if a.output == "-":
        sys.stdout.buffer.write(raw)
        sys.stdout.flush()
    else:
        try:
            Path(a.output).write_bytes(raw)
        except OSError as e:
            raise UsageError(f"can't write {a.output}: {e}") from e
    sample = f"the newest {got:,} of {matched:,}" if matched > got else f"all {got:,}"
    _info(f"fetched {sample} log lines from the last {a.since} of '{a.index}'"
          + ("" if a.output == "-" else f" into {a.output} (it holds logged values: delete it after the check)"),
          "logs_fetched")
    return 0


def cmd_lint_code(a: argparse.Namespace) -> int:
    from logwarden import lint
    try:
        unknown = set(a.exclude_rule) - lint.rule_ids()
        if unknown:
            raise UsageError(f"lint-code: unknown rule {', '.join(sorted(unknown))}; see 'logwarden lint-code --list'")
        if a.list:
            print("\n".join(sorted(lint.rule_ids())))
            return 0
        result = lint.run(a.paths, exclude=a.exclude_rule)
    except lint.LintError as e:
        raise UsageError(f"lint-code: {e}") from e
    if a.sarif:
        from logwarden import sarif
        _write(Path(a.sarif), json.dumps(sarif.from_lint(result), indent=2, ensure_ascii=False) + "\n")
    if a.codequality:
        from logwarden import codequality
        _write(Path(a.codequality), codequality.dumps(codequality.from_lint(result)))
    print(json.dumps(result, indent=2, ensure_ascii=False) if a.json else lint.text(result, a.max))
    return 0 if result["ok"] else 1


def cmd_log_coverage(a: argparse.Namespace) -> int:
    from logwarden import logcoverage as lc
    levels = [x.strip() for x in a.levels.split(",") if x.strip()] if a.levels else None
    try:
        result = lc.measure(a.paths, coverage=Path(a.coverage) if a.coverage else None,
                            logs=Path(a.logs) if a.logs else None, levels=levels,
                            receiver=a.receiver or lc.RECEIVER, excludes=a.exclude,
                            fail_under=a.fail_under)
    except lc.LogCoverageError as e:
        raise UsageError(f"log-coverage: {e}") from e
    if a.markdown:
        _write(Path(a.markdown), lc.markdown(result, a.max))
    print(json.dumps(result, indent=2, ensure_ascii=False) if a.json else lc.text(result, a.max))
    return 0 if result["ok"] else 1


def cmd_baseline(a: argparse.Namespace) -> int:
    cwd = Path.cwd()
    text = _read_input(a.input)
    fields, policy = resolve(cwd, fields=a.fields, registry=a.registry, no_baseline=True, args=a)
    cfg_path = config.load(cwd).baseline
    target = Path(a.output or cfg_path or cwd / config.DEFAULT_BASELINE)
    try:
        found, _, _ = check.evaluate(text, fields=fields,
                                     registry=Path(a.registry) if a.registry else None,
                                     upstreams=policy.upstreams, policy=policy)
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


def _init_from(a: argparse.Namespace, d: Path) -> None:
    """Declare the custom fields seen in real logs, list the ones to fix, turn on `standard`."""
    from logwarden import fields as lwfields
    text = _read_input(a.source_logs)
    cfg = _load_config(d)
    target = Path(cfg.fields) if cfg.fields else d / FIELDS_DEFAULT
    policy = check.Policy(disabled={}, source=source_of(cfg, a), profile="standard")
    try:
        found, inp = check._evaluate(text, fields=target if target.is_file() else None, registry=None,
                                     out=None, upstreams=[], policy=policy)
    except check.CheckError as e:
        raise UsageError(f"init --from: {e}") from e
    p = lwfields.propose(found, inp.records)
    source = "-" if a.source_logs == "-" else Path(a.source_logs).name
    added, flagged = lwfields.write(target, p, source)
    kept = len(p.declare) - len(added)
    print(f"{target}: declared {len(added)} new field{'' if len(added) == 1 else 's'}"
          + (f" ({kept} already there)" if kept else "")
          + (f"; {len(flagged)} to fix in the code, listed at the end" if flagged else ""))
    print(lwfields.set_profile(d))
    print("next: review the fields file (describe each field, drop any that semconv already names), "
          "then run 'logwarden check <logs>'")


def cmd_init(a: argparse.Namespace) -> int:
    d = Path(a.dir)
    if not d.is_dir():
        raise UsageError(f"init: no such directory: {a.dir}")
    if a.source_logs:
        _init_from(a, d)
        if a.agent:
            snippet = data.path("templates/AGENTS.snippet.md").read_text(encoding="utf-8")
            print(upsert_block(d / "AGENTS.md", snippet))
        return 0
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
    """Download Weaver and the semantic conventions, so later checks work offline."""
    try:
        print(weaver.find())
        manifest = data.path("registry") / "manifest.yaml"
        model = weaver.semconv_model(weaver.semconv_version(manifest))
        _info(f"semantic conventions: {model}", "semconv_ready")
        model = weaver.genai_model(weaver.genai_version(manifest))
        _info(f"GenAI semantic conventions: {model}", "semconv_ready")
        with tempfile.TemporaryDirectory(prefix="logwarden-") as tmp:
            chain = check.base_chain(Path(tmp))
            if not chain.is_relative_to(tmp):
                _info(f"registry for checks: {chain.parent}", "semconv_ready")
    except weaver.WeaverError as e:
        raise UsageError(str(e)) from e
    return 0


def cmd_mcp(_: argparse.Namespace) -> int:
    try:
        from logwarden import mcp_server
    except ImportError as e:
        raise UsageError("the MCP server needs the 'mcp' extra: "
                         "pip install 'logwarden[mcp]' (or uvx --from 'logwarden[mcp]' logwarden mcp)") from e
    selflog.route_library_logs()   # JSON mode: the MCP SDK's own log lines as JSON records
    mcp_server.main()
    return 0


def _report_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--compare", metavar="FILE",
                   help="report new and fixed findings against a snapshot (or baseline) of the base branch")
    p.add_argument("--snapshot", metavar="FILE", help="save what this run found, to --compare with later")
    p.add_argument("--html", metavar="FILE", help="write a self-contained HTML report")
    p.add_argument("--markdown", metavar="FILE", help="write a Markdown summary (PR comment, job summary)")
    p.add_argument("--sarif", metavar="FILE",
                   help="write SARIF for code scanning: findings on the source line that logged them "
                        "(needs code.file.path / code.line.number in the lines)")
    p.add_argument("--codequality", metavar="FILE",
                   help="write a GitLab Code Quality report (artifacts:reports:codequality): "
                        "findings in the merge request")
    p.add_argument("--source-root", metavar="DIR",
                   help="path prefix to remove from code.file.path in SARIF and Code Quality "
                        "(default: the current directory)")
    p.add_argument("--metrics", metavar="FILE",
                   help="write per-service metrics in Prometheus text format (Pushgateway, textfile collector)")
    p.add_argument("--pushgateway", metavar="URL",
                   help="push the per-service metrics to a Prometheus Pushgateway, replacing the group "
                        "(default group: /metrics/job/logwarden; credentials: $LOGWARDEN_PUSHGATEWAY_AUTH)")


def _profile_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument("--profile", choices=list(check.PROFILES),
                   help="rules to check: essential (sensitive data, non-JSON lines, required fields; "
                        "a first step on any service) or standard (every rule). Default: config, then standard")


def _input_args(p: argparse.ArgumentParser) -> None:
    from logwarden.sources import FORMATS
    g = p.add_argument_group("input", "Kibana / Elasticsearch exports and records wrapped by a log "
                                      "shipper (defaults: [source] in the config)")
    g.add_argument("--format", choices=FORMATS,
                   help="jsonl (JSON lines, elasticdump), es (Elasticsearch _search response), "
                        "csv (Kibana CSV report); default: detected")
    g.add_argument("--message-field", metavar="NAME",
                   help="parse the service's JSON line from this field, e.g. log (Fluent Bit)")
    g.add_argument("--ignore-field", action="append", metavar="NAME",
                   help="field added by the pipeline, not the service, e.g. kubernetes (repeatable)")
    g.add_argument("--service-field", metavar="NAME", help="field that names the service (default service.name)")
    g.add_argument("--service", action="append", metavar="NAME",
                   help="check only this service (repeatable; '(none)' for lines without one)")


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # invalid arguments: exit 2, as a JSON record in JSON mode
        if selflog.fmt() == "json":
            _err(f"{self.prog}: {message}", "command_failed", "error", "invalid_arguments")
            sys.exit(2)
        super().error(message)


def _log_format(argv: list[str]) -> list[str]:
    """Take --log-format out of argv (any position before `--`) and apply it."""
    rest, i = [], 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--":
            rest += argv[i:]
            break
        if arg == "--log-format" or arg.startswith("--log-format="):
            value = arg.partition("=")[2] if "=" in arg else (argv[i + 1] if i + 1 < len(argv) else "")
            i += 1 if "=" in arg else 2
            if value not in selflog.FORMATS:
                raise UsageError(f"--log-format must be text or json, not {value!r}")
            selflog.set_format(value)
            continue
        rest.append(arg)
        i += 1
    return rest


def parser() -> argparse.ArgumentParser:
    p = _Parser(
        prog="logwarden",
        description="Guardrail for structured logs. Exit codes: 0 no violations, "
                    "1 violations found, 2 usage or setup error.",
        epilog="Docs: https://github.com/Ingeniator/logwarden (docs/standard.md, docs/rules.md)")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--log-format", choices=selflog.FORMATS,
                   help=f"logwarden's own messages on stderr: text (default) or json, records that pass "
                        f"the standard (also {selflog.ENV}=json). Allowed before or after the command")
    sub = p.add_subparsers(dest="cmd", metavar="command")

    c = sub.add_parser("check", help="check logs: JSON lines, Kibana CSV or Elasticsearch exports (- reads stdin)")
    c.add_argument("input", help="log file, or - for stdin")
    c.add_argument("--json", action="store_true", help="machine-readable result (CI tools, AI agents)")
    c.add_argument("--fields", help=f"project-only fields file (default: config, then ./{FIELDS_DEFAULT})")
    c.add_argument("--registry", help="full Weaver registry overlay (advanced; replaces --fields)")
    c.add_argument("-o", metavar="DIR", help="keep samples.json, live_check.json, result.json and the "
                                              "reports (report.html, report.md, snapshot.json) in DIR")
    c.add_argument("--max", type=int, default=20, help="findings shown in text output (default 20)")
    c.add_argument("--baseline", metavar="FILE", help="fail only on findings not in FILE (default: config)")
    c.add_argument("--no-baseline", action="store_true", help="ignore the configured baseline")
    c.add_argument("--signal", choices=("logs", "metrics"), default="logs",
                   help="what the input is: logs (default) or metrics, Prometheus / OpenMetrics text "
                        "(experimental: rules and options for metrics may still change)")
    c.add_argument("--series-budget", type=int, default=1000, metavar="N",
                   help="metrics: series per metric before adr_metric_cardinality (default 1000)")
    _profile_arg(c)
    _report_args(c)
    _input_args(c)
    c.set_defaults(fn=cmd_check)

    b = sub.add_parser("baseline", help="record current findings so check fails only on new ones")
    b.add_argument("input", help="log file, or - for stdin")
    b.add_argument("--output", metavar="FILE",
                   help=f"baseline file (default: config 'baseline', then ./{config.DEFAULT_BASELINE})")
    b.add_argument("--fields", help="project-only fields file (as for check)")
    b.add_argument("--registry", help="full Weaver registry overlay (as for check)")
    _profile_arg(b)
    _input_args(b)
    b.set_defaults(fn=cmd_baseline)

    cap = sub.add_parser("capture", help="receive logs over OTLP/HTTP, write them as JSON lines, then check them",
                         description="Receive OTLP/HTTP logs (protobuf or JSON) and write them as JSON "
                                     "lines. With a command: run it with OTEL_* pointing here, then check. "
                                     "Without: receive until Ctrl-C or SIGTERM, then check.")
    cap.add_argument("-o", "--output", default="logwarden-capture.jsonl",
                     help="where to write the lines (default: logwarden-capture.jsonl)")
    cap.add_argument("--host", default="127.0.0.1", help="listen address (0.0.0.0 in a container)")
    cap.add_argument("--port", type=int, help="listen port (default 4318; a free port when running a command)")
    cap.add_argument("--no-check", action="store_true", help="only write the lines")
    cap.add_argument("--json", action="store_true", help="machine-readable check result")
    cap.add_argument("--fields", help="project-only fields file (as for check)")
    cap.add_argument("--max", type=int, default=20, help="findings shown in text output (default 20)")
    cap.add_argument("--baseline", metavar="FILE", help="as for check")
    cap.add_argument("--no-baseline", action="store_true", help="as for check")
    _profile_arg(cap)
    _report_args(cap)
    cap.add_argument("command", nargs=argparse.REMAINDER, help="-- command to run, e.g. -- npm test")
    cap.set_defaults(fn=cmd_capture, registry=None, o=None)

    cat = sub.add_parser("catalog", help="list the event names a run logs, or write the event catalog file",
                         description="List every event in a log file (count, levels, fields). With -o, "
                                     "add new events to the catalog file (TOML), keeping existing entries.")
    cat.add_argument("input", help="log file, or - for stdin")
    cat.add_argument("-o", "--output", metavar="FILE", help="catalog file to create or update, "
                                                           "e.g. logwarden-events.toml")
    fmt = cat.add_mutually_exclusive_group()
    fmt.add_argument("--json", action="store_true", help="machine-readable list")
    fmt.add_argument("--markdown", action="store_true", help="Markdown table (with descriptions from the catalog)")
    _input_args(cat)
    cat.set_defaults(fn=cmd_catalog)

    fe = sub.add_parser("fetch", help="fetch recent logs from Elasticsearch for check (scheduled checks)",
                        description="Write the newest log lines of a time window from Elasticsearch as a "
                                    "_search response, for 'logwarden check'. Credentials: LOGWARDEN_ES_AUTH "
                                    "('ApiKey ...', 'Basic ...' or 'user:password'). The file holds the "
                                    "logged values: don't publish it.")
    fe.add_argument("--es", required=True, metavar="URL", help="Elasticsearch URL, e.g. https://es:9200")
    fe.add_argument("--index", required=True, metavar="PATTERN", help="index or data stream, e.g. 'logs-*'")
    fe.add_argument("--since", default="1h", help="time window: 30m, 1h, 1d (default 1h)")
    fe.add_argument("--size", type=int, default=10_000,
                    help="newest lines to fetch, at most 10000 (default); more matching lines make it a sample")
    fe.add_argument("--time-field", default="@timestamp", help="timestamp field (default @timestamp)")
    fe.add_argument("--query", metavar="QUERY",
                    help="Lucene query string to narrow it down, e.g. 'kubernetes.namespace_name:shop'")
    fe.add_argument("--ca", metavar="FILE", help="CA certificate for https (or $LOGWARDEN_ES_CA)")
    fe.add_argument("--insecure", action="store_true", help="don't verify the TLS certificate")
    fe.add_argument("-o", "--output", default="logwarden-es.json", help="where to write (- for stdout)")
    fe.set_defaults(fn=cmd_fetch)

    lc = sub.add_parser("lint-code", help="check source code with logwarden's Semgrep rules (needs semgrep)",
                        description="Run logwarden's Semgrep rules (Python, JavaScript/TypeScript, Go, Java) "
                                    "on source code.")
    lc.add_argument("paths", nargs="*", help="files or directories (default: .)")
    lc.add_argument("--json", action="store_true", help="machine-readable result")
    lc.add_argument("--max", type=int, default=50, help="findings shown in text output (default 50)")
    lc.add_argument("--exclude-rule", action="append", default=[], metavar="ID",
                    help="skip a rule, e.g. logwarden-print-python (repeatable)")
    lc.add_argument("--list", action="store_true", help="list the rule ids")
    lc.add_argument("--sarif", metavar="FILE", help="also write the findings as SARIF (code scanning)")
    lc.add_argument("--codequality", metavar="FILE",
                    help="also write the findings as a GitLab Code Quality report")
    lc.set_defaults(fn=cmd_lint_code)

    cov = sub.add_parser("log-coverage", help="which log calls the tests run (Python source + coverage data)",
                         description="Find the log calls in Python source and report which ones the tests "
                                     "ran (--coverage) or that wrote a line (--logs), per file and level.")
    cov.add_argument("paths", nargs="*", help="source files or directories (default: .)")
    cov.add_argument("--coverage", metavar="FILE",
                     help="coverage.py data: .coverage, `coverage json` or Cobertura XML (`coverage xml`)")
    cov.add_argument("--logs", metavar="FILE",
                     help="lines captured from the tests (JSON lines with code.file.path, code.line.number)")
    cov.add_argument("--levels", metavar="L1,L2",
                     help="count only these levels (debug, info, warning, error, exception, critical)")
    cov.add_argument("--fail-under", type=float, metavar="PCT",
                     help="exit 1 when fewer than PCT%% of the counted calls are reached (or wrote a line, with --logs)")
    cov.add_argument("--receiver", metavar="REGEX",
                     help="what a logger looks like in a call `<receiver>.info(...)` (default: log, logger, "
                          "logging, structlog or slog as a word)")
    cov.add_argument("--exclude", action="append", default=[], metavar="GLOB",
                     help="skip matching files (repeatable); tests/, .venv/ and the like are always skipped")
    cov.add_argument("--json", action="store_true", help="machine-readable result")
    cov.add_argument("--markdown", metavar="FILE", help="also write a Markdown summary (job summary, PR comment)")
    cov.add_argument("--max", type=int, default=50, help="calls listed in text and Markdown output (default 50)")
    cov.set_defaults(fn=cmd_log_coverage)

    r = sub.add_parser("rules", help="list rules, or explain one")
    r.add_argument("rule_id", nargs="?")
    r.set_defaults(fn=cmd_rules)

    i = sub.add_parser("init", help="add logwarden files to a project")
    i.add_argument("dir", nargs="?", default=".")
    i.add_argument("--agent", action="store_true", help="also add a logwarden section to AGENTS.md")
    i.add_argument("--from", dest="source_logs", metavar="LOGS",
                   help="declare the custom fields these logs use in the fields file (not synonyms or "
                        "sensitive fields: those are listed to fix), and set profile = \"standard\"")
    _input_args(i)
    i.set_defaults(fn=cmd_init)

    s = sub.add_parser("setup", help="download weaver and the semantic conventions if missing; print weaver's path")
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
    selflog.set_format(None)
    try:
        argv = _log_format(list(sys.argv[1:] if argv is None else argv))
    except UsageError as e:
        _err(str(e), error_type="invalid_arguments")
        return 2
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
    except Exception as e:
        if selflog.fmt() != "json":
            raise
        selflog.emit("command_crashed", f"unexpected error in '{a.cmd}': {type(e).__name__}", "error", exc=e)
        return 1   # what Python returns for an uncaught exception, as in text mode
