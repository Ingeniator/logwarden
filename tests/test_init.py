"""init writes the fields template and an idempotent AGENTS.md section; no skill copy (plugin replaces it)."""


def test_init_agent_twice(cli, tmp_path):
    (tmp_path / "AGENTS.md").write_text("# Project\n\nExisting text.\n")
    for _ in range(2):
        r = cli("init", tmp_path, "--agent")
        assert r.returncode == 0, r.stderr
    agents = (tmp_path / "AGENTS.md").read_text()
    assert agents.count("<!-- logwarden:begin -->") == 1
    assert agents.startswith("# Project\n\nExisting text.\n")
    assert "## Logging (logwarden)" in agents
    assert (tmp_path / "logwarden-fields.yaml").is_file()
    assert not (tmp_path / ".claude").exists()
    assert "/plugin install logwarden" in r.stdout


def test_init_keeps_existing_fields(cli, tmp_path):
    (tmp_path / "logwarden-fields.yaml").write_text("groups: []  # mine\n")
    cli("init", tmp_path)
    assert "# mine" in (tmp_path / "logwarden-fields.yaml").read_text()


def test_init_missing_dir(cli, tmp_path):
    assert cli("init", tmp_path / "nope").returncode == 2
