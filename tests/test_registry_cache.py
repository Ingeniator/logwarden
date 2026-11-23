"""v0.10.3: the semconv + GenAI registry chain is built once in the cache, not copied per run."""
import json
import os
import shutil
import sys

import pytest

from conftest import run_cli
from logwarden import check, data, weaver
from test_new_rules import _line

MANIFEST = data.path("registry") / "manifest.yaml"
REPLACED = ("gen-ai", "mcp", "openai")


@pytest.fixture
def cache(tmp_path, monkeypatch):
    """An empty cache dir with the (real, already downloaded) archives linked in."""
    semconv = weaver.semconv_model(weaver.semconv_version(MANIFEST), log=lambda m: None)
    genai = weaver.genai_model(weaver.genai_version(MANIFEST), log=lambda m: None)
    binary = weaver.find(log=lambda m: None)
    c = tmp_path / "cache"
    c.mkdir()
    for model in (semconv, genai):
        (c / model.parent.parent.name).symlink_to(model.parent.parent, target_is_directory=True)
    for var in ("LOGWARDEN_SEMCONV", "LOGWARDEN_SEMCONV_GENAI"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LOGWARDEN_CACHE", str(c))
    monkeypatch.setenv("WEAVER", binary)
    return c


def _chains(c):
    return sorted(p.name for p in c.iterdir() if p.name.startswith("registry-"))


def _copies(monkeypatch):
    calls = []
    real = shutil.copytree
    monkeypatch.setattr(check.shutil, "copytree", lambda *a, **k: calls.append(a[0]) or real(*a, **k))
    return calls


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_claim1_built_once(cache, tmp_path, monkeypatch):
    calls = _copies(monkeypatch)
    first = check.base_chain(tmp_path / "run1")
    assert calls and first.parent.parent == cache
    assert _chains(cache) == ["registry-semconv-1.45.0-genai-0.1.0-1"]
    assert not any((first.parent / "semconv" / d).exists() for d in REPLACED)
    assert (first.parent / "semconv" / "http").is_dir()
    stamp = (first / "manifest.yaml").stat().st_mtime_ns
    calls.clear()
    reg = check.base_registry(tmp_path / "run2")
    assert calls == [] and check.base_chain(tmp_path / "run3") == first
    assert (first / "manifest.yaml").stat().st_mtime_ns == stamp
    # Only logwarden's own files are in the run's directory.
    assert sorted(p.name for p in (tmp_path / "run2").iterdir()) == ["logwarden"]
    assert str(first) in (reg / "manifest.yaml").read_text(encoding="utf-8")


@pytest.mark.weaver
@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_claim1_checks_use_the_cached_chain(cache, tmp_path):
    line = _line("llm_call_completed", **{"gen_ai.provider.name": "openai", "gen_ai.request.model": "m",
                                          "gen_ai.usage.input_tokens": 5}) + "\n"
    for _ in range(2):
        r = run_cli("check", "-", "--json", stdin=line, cwd=tmp_path)
        assert json.loads(r.stdout)["violations"] == 0, r.stdout + r.stderr
    assert _chains(cache) == ["registry-semconv-1.45.0-genai-0.1.0-1"]


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_claim3_local_copies_are_not_cached(cache, tmp_path, monkeypatch):
    monkeypatch.setenv("LOGWARDEN_SEMCONV", str(weaver.semconv_model("1.45.0")))
    chain = check.base_chain(tmp_path / "run")
    assert chain == tmp_path / "run" / "chain" / "genai" and _chains(cache) == []


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0,
                    reason="read-only directories: POSIX, not root")
def test_claim3_read_only_cache(cache, tmp_path):
    cache.chmod(0o555)
    try:
        chain = check.base_chain(tmp_path / "run")
        assert chain == tmp_path / "run" / "chain" / "genai" and _chains(cache) == []
        assert not any((tmp_path / "run" / "chain" / "semconv" / d).exists() for d in REPLACED)
    finally:
        cache.chmod(0o755)


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_claim3_leftovers_and_a_moved_cache(cache, tmp_path, monkeypatch):
    name = "registry-semconv-1.45.0-genai-0.1.0-1"
    (cache / f"{name}.tmp-99999" / "semconv").mkdir(parents=True)   # an interrupted build
    chain = check.base_chain(tmp_path / "run")
    assert chain == cache / name / "genai"
    # The cache copied to another directory: the chain is rebuilt there, pointing at itself.
    moved = tmp_path / "moved"
    shutil.copytree(cache, moved, symlinks=True)
    monkeypatch.setenv("LOGWARDEN_CACHE", str(moved))
    again = check.base_chain(tmp_path / "run2")
    assert again == moved / name / "genai"
    assert f"registry_path: {(moved / name / 'semconv').as_posix()}" in (again / "manifest.yaml").read_text()


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_claim5_setup_builds_it(cache, tmp_path):
    r = run_cli("setup", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert _chains(cache) == ["registry-semconv-1.45.0-genai-0.1.0-1"]
    assert "registry for checks" in r.stderr
