"""v0.6.0 claims 9-10: Semgrep rules and `logwarden lint-code`."""
import json
import re
import shutil
import subprocess

import pytest

from conftest import EXAMPLES, ROOT
from logwarden import data, lint, patterns

FIXTURES = ROOT / "tests" / "semgrep"
RULES = data.path("semgrep/logwarden.yml")
try:
    SEMGREP = lint.find_semgrep()
except lint.LintError:
    SEMGREP = None
needs_semgrep = pytest.mark.skipif(SEMGREP is None, reason="semgrep not available (no Windows build)")


def _fixtures(tmp_path):
    """Semgrep skips tests/ directories by default, and --test wants the rules beside the files."""
    d = tmp_path / "fixtures"
    shutil.copytree(FIXTURES, d)
    shutil.copy(RULES, d / "logwarden.yml")
    return d


def _rule_regexes(kind):
    text = RULES.read_text(encoding="utf-8")
    blocks = re.findall(rf"- id: logwarden-{kind}-\w+\n(.*?)(?=\n  - id: |\Z)", text, re.S)
    return {re.search(r"metavariable: \$K\n\s+regex: '([^']+)'", b).group(1) for b in blocks}


def test_claim9_banned_list_equals_rego():
    rego = data.path("policies/logwarden.rego").read_text(encoding="utf-8")
    banned = set(re.findall(r'^\t"([a-z_]+)":', rego.split("banned := {")[1].split("}")[0], re.M))
    (regex,) = _rule_regexes("banned-key")
    assert set(regex.strip("^()$").split("|")) == banned


def test_claim9_sensitive_keys_agree_with_patterns():
    (regex,) = _rule_regexes("sensitive-key")
    names = ["password", "db_password", "auth.token", "api_key", "apikey", "access-key", "private_key",
             "authorization", "set_cookie", "credentials", "client_secret",
             "user_id", "token_count", "passwords_checked", "tokens"]
    for n in names:
        assert bool(re.match(regex, n)) == patterns.is_sensitive_key(n), n


def test_claim9_every_rule_has_fixtures():
    ids = set(re.findall(r"- id: (logwarden-[\w-]+)", RULES.read_text(encoding="utf-8")))
    assert len(ids) == 16
    text = "".join(p.read_text(encoding="utf-8") for p in FIXTURES.iterdir())
    for i in ids:
        assert f"ruleid: {i}\n" in text and f"ok: {i}\n" in text, i


@needs_semgrep
def test_claim9_semgrep_test(tmp_path):
    r = subprocess.run([*SEMGREP, "--test", str(_fixtures(tmp_path)), "--metrics=off"],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "16/16: ✓ All tests passed" in r.stdout + r.stderr


@needs_semgrep
def test_claim10_lint_code(cli, tmp_path):
    d = _fixtures(tmp_path)
    bad = cli("lint-code", "--json", d)
    assert bad.returncode == 1, bad.stderr
    result = json.loads(bad.stdout)
    expected = sum(p.read_text(encoding="utf-8").count("ruleid:") for p in FIXTURES.iterdir())
    assert not result["ok"] and len(result["findings"]) == expected == 37
    assert {f["rule"].rsplit("-", 1)[0] for f in result["findings"]} == {
        "logwarden-event-name", "logwarden-sensitive-key", "logwarden-banned-key", "logwarden-print"}
    first = next(f for f in result["findings"] if f["path"].endswith("logwarden.py"))
    assert first["code"] == 'log.info(f"Imported {n} rows")' and first["message"].startswith("§4")
    text = cli("lint-code", d / "logwarden.go")
    assert text.returncode == 1 and "logwarden.go:16\tlogwarden-event-name-go" in text.stdout

    clean = cli("lint-code", EXAMPLES / "recipes", ROOT / "src" / "logwarden")
    assert clean.returncode == 0 and "0 findings" in clean.stdout, clean.stdout + clean.stderr
    excluded = [x for r in ("print", "event-name", "sensitive-key", "banned-key")
                for x in ("--exclude-rule", f"logwarden-{r}-python")]
    skipped = cli("lint-code", d / "logwarden.py", *excluded)
    assert skipped.returncode == 0, skipped.stdout + skipped.stderr
    assert cli("lint-code", "--exclude-rule", "nope", d).returncode == 2
    listed = cli("lint-code", "--list")
    assert listed.returncode == 0 and len(listed.stdout.split()) == 16


def test_claim10_missing_semgrep(cli, tmp_path, monkeypatch):
    r = cli("lint-code", tmp_path, env={"SEMGREP": str(tmp_path / "no-semgrep")})
    assert r.returncode == 2 and "Semgrep is needed" in r.stderr
    monkeypatch.delenv("SEMGREP", raising=False)
    monkeypatch.setattr(lint.sys, "executable", str(tmp_path / "python"))
    if lint.sys.platform != "win32":   # no semgrep, but uv: run it in its own environment
        monkeypatch.setattr(lint.shutil, "which", lambda name: "/bin/uvx" if name == "uvx" else None)
        assert lint.find_semgrep() == ["/bin/uvx", "--quiet", f"semgrep=={lint.SEMGREP_VERSION}"]
    monkeypatch.setattr(lint.shutil, "which", lambda name: None)
    with pytest.raises(lint.LintError, match="Semgrep is needed"):
        lint.find_semgrep()
