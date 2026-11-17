"""v0.3.0 claim 5: [tool.logwarden] and logwarden.toml are read; unknown keys warn."""
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
    (tmp_path / "logwarden.toml").write_text('fields = "a.yaml"\nbaseline = "x"\n')
    assert any("unknown key 'baseline'" in w for w in config.load(tmp_path).warnings)


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
