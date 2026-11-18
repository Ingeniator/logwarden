"""v0.3.0 claim 5: [tool.logwarden] and logwarden.toml are read; unknown keys warn."""
import json

import pytest

from logwarden import cli as lwcli
from logwarden import config
from test_cli import FIELDS, LINE


def test_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[tool.logwarden]\nfields = "conf/f.yaml"\n')
    cfg = config.load(tmp_path)
    assert cfg.fields == str(tmp_path / "conf/f.yaml") and not cfg.warnings


def test_logwarden_toml_wins(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[tool.logwarden]\nfields = "a.yaml"\n')
    (tmp_path / "logwarden.toml").write_text('fields = "b.yaml"\n')
    cfg = config.load(tmp_path)
    assert cfg.fields.endswith("b.yaml")
    assert any("both" in w for w in cfg.warnings)


def test_unknown_key_warns(tmp_path):
    (tmp_path / "logwarden.toml").write_text('fields = "a.yaml"\nupstreams = "x"\n')
    assert any("unknown key 'upstreams'" in w for w in config.load(tmp_path).warnings)


def test_no_config(tmp_path):
    cfg = config.load(tmp_path)
    assert cfg.fields is None and not cfg.warnings


def test_invalid_toml_warns(tmp_path):
    (tmp_path / "logwarden.toml").write_text("fields = \n")
    assert any("could not read" in w for w in config.load(tmp_path).warnings)


def test_precedence(tmp_path, monkeypatch):
    for name in ("arg.yaml", "env.yaml", "conf.yaml", lwcli.FIELDS_DEFAULT):
        (tmp_path / name).write_text(FIELDS)
    (tmp_path / "logwarden.toml").write_text('fields = "conf.yaml"\n')
    monkeypatch.setenv("LOGWARDEN_FIELDS", str(tmp_path / "env.yaml"))
    assert lwcli.resolve_fields(str(tmp_path / "arg.yaml"), None, tmp_path).name == "arg.yaml"
    assert lwcli.resolve_fields(None, None, tmp_path).name == "env.yaml"
    monkeypatch.delenv("LOGWARDEN_FIELDS")
    assert lwcli.resolve_fields(None, None, tmp_path).name == "conf.yaml"
    (tmp_path / "logwarden.toml").unlink()
    assert lwcli.resolve_fields(None, None, tmp_path).name == lwcli.FIELDS_DEFAULT
    assert lwcli.resolve_fields(None, str(tmp_path), tmp_path) is None  # --registry replaces fields


@pytest.mark.weaver
def test_pyproject_fields_used_by_check(cli, tmp_path):
    (tmp_path / "fields").mkdir()
    (tmp_path / "fields" / "mine.yaml").write_text(FIELDS)
    (tmp_path / "pyproject.toml").write_text('[tool.logwarden]\nfields = "fields/mine.yaml"\nnope = 1\n')
    r = cli("check", "-", stdin=LINE, cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "unknown key 'nope'" in r.stderr


# v0.4.0 claim 4: `disable` needs a reason, and disabled rules stay visible.
def test_disable_parsed(tmp_path):
    (tmp_path / "logwarden.toml").write_text('disable = { adr_event_name = " legacy events " }\n')
    cfg = config.load(tmp_path)
    assert cfg.disable == {"adr_event_name": "legacy events"} and not cfg.errors


@pytest.mark.parametrize("value, error", [
    ('["adr_event_name"]', "needs a reason per rule"),
    ('{ adr_event_name = "" }', "needs a non-empty reason"),
    ('{ adr_event_name = 1 }', "needs a non-empty reason"),
    ('{ no_such_rule = "x" }', "unknown rule 'no_such_rule'"),
])
def test_disable_errors(tmp_path, cli, value, error):
    (tmp_path / "logwarden.toml").write_text(f"disable = {value}\n")
    assert any(error in e for e in config.load(tmp_path).errors)
    r = cli("check", "-", stdin=LINE, cwd=tmp_path)
    assert r.returncode == 2 and error in r.stderr


@pytest.mark.weaver
def test_disabled_rule_does_not_fail_and_is_listed(cli, tmp_path):
    (tmp_path / "logwarden.toml").write_text(
        'disable = { adr_non_json_line = "test runner output", missing_attribute = "fields file pending" }\n')
    text = LINE + "plain text\n"
    r = cli("check", "-", "--json", stdin=text, cwd=tmp_path)
    res = json.loads(r.stdout)
    assert r.returncode == 0 and res["ok"]
    assert res["disabled"] == [
        {"rule": "adr_non_json_line", "reason": "test runner output", "count": 1},
        {"rule": "missing_attribute", "reason": "fields file pending", "count": 1}]
    text_out = cli("check", "-", stdin=text, cwd=tmp_path).stdout
    assert "disabled: adr_non_json_line (not counted: 1): test runner output" in text_out
