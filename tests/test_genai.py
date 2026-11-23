"""v0.10.2: the GenAI semantic conventions are bundled, so the gen_ai.* fields that the standard
asks for pass (claims 6-9)."""
import hashlib
import json
import re
import subprocess

import pytest
import yaml

from conftest import ROOT, run_cli
from logwarden import check, data, weaver
from test_new_rules import _line

MANIFEST = data.path("registry/manifest.yaml")
DEPRECATED = data.path("registry/genai-deprecated.yaml")


def _genai() -> "weaver.Path":
    return weaver.genai_model(weaver.genai_version(MANIFEST), log=lambda m: None)


def _keys(model) -> set[str]:
    """Attributes a GenAI model defines (definition/2 files: `- key: name`)."""
    return {m for f in model.rglob("*.yaml") for m in re.findall(r"^\s*- key:\s*(\S+)\s*$",
                                                                 f.read_text(encoding="utf-8"), re.M)}


def _old_names() -> dict[str, str | None]:
    attrs = yaml.safe_load(DEPRECATED.read_text(encoding="utf-8"))["groups"][0]["attributes"]
    return {a["id"]: a["deprecated"].get("renamed_to") for a in attrs}


def test_pins():
    assert weaver.genai_version(MANIFEST) in weaver.GENAI_SHA256
    assert weaver.semconv_version(MANIFEST) in weaver.SEMCONV_SHA256


def _check(tmp_path, lines, *args):
    (tmp_path / "logs.jsonl").write_text("".join(x + "\n" for x in lines), encoding="utf-8")
    r = run_cli("check", "logs.jsonl", "--profile", "standard", "--json", *args, cwd=tmp_path)
    assert r.returncode in (0, 1), r.stderr
    return json.loads(r.stdout)


@pytest.mark.weaver
def test_claim6_genai_fields_pass(tmp_path):
    res = _check(tmp_path, [_line("llm_called", **{
        "gen_ai.provider.name": "openai", "gen_ai.request.model": "gpt-4o",
        "gen_ai.usage.input_tokens": 12, "gen_ai.usage.output_tokens": 30,
        "gen_ai.operation.name": "chat", "gen_ai.tool.name": "search", "gen_ai.request.top_k": 5,
        "openai.response.service_tier": "default", "mcp.session.id": "s-1"})])
    assert res["violations"] == 0, res["findings"]


@pytest.mark.weaver
def test_claim6_no_genai_field_is_deprecated(tmp_path):
    keys = sorted(_keys(_genai()))
    assert len(keys) > 90
    res = _check(tmp_path, [_line("one_field", **{k: "x"}) for k in keys])
    deprecated = sorted({f["field"] for f in res["findings"] if f["rule"] == "deprecated"})
    assert deprecated == [], deprecated
    assert not any(f["rule"] == "missing_attribute" for f in res["findings"])


def test_claim7_old_names_are_gone_from_genai_and_point_at_genai_fields():
    keys, old = _keys(_genai()), _old_names()
    assert len(old) == 11
    assert not set(old) & keys                                   # really removed from GenAI
    assert {new for new in old.values() if new} <= keys          # the new name exists there


@pytest.mark.weaver
def test_claim7_old_names_say_what_to_use(tmp_path):
    old = _old_names()
    res = _check(tmp_path, [_line("one_field", **{k: "x"}) for k in sorted(old)] + [_line("m", model="gpt-4o")])
    got = {f["field"]: f["message"] for f in res["findings"] if f["rule"] == "deprecated"}
    assert set(got) == set(old)
    for name, new in old.items():
        assert (f"`{new}`" in got[name]) if new else "obsoleted" in got[name], got[name]
    hint = next(f["message"] for f in res["findings"] if f["rule"] == "adr_banned_synonym")
    assert hint.endswith("use gen_ai.request.model")
    res = _check(tmp_path, [_line("m", **{"gen_ai.request.model": "gpt-4o"})])
    assert res["violations"] == 0, res["findings"]


@pytest.mark.weaver
def test_claim8_each_field_is_defined_once(tmp_path):
    reg = check.base_registry(tmp_path / "base")
    sc = check.base_chain(tmp_path / "x").parent / "semconv"     # the cached chain (v0.10.3)
    assert not (sc / "gen-ai").exists() and (sc / "http").is_dir()
    for name in ("mcp", "openai"):
        assert not (sc / name).exists()
    p = subprocess.run([weaver.find(), "registry", "resolve", "-r", str(reg), "--include-unreferenced",
                        "--format", "json", "--skip-policies"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    twice = set(re.findall(r"attribute id `([^`]+)` is declared multiple", re.sub(r"\s+", " ", p.stderr)))
    # semconv and GenAI both define these two, identically and not deprecated.
    assert twice == {"aws.bedrock.guardrail.id", "aws.bedrock.knowledge_base.id"}, twice


def test_claim9_genai_download_checked_and_env(tmp_path, monkeypatch):
    monkeypatch.setenv("LOGWARDEN_CACHE", str(tmp_path))
    monkeypatch.delenv("LOGWARDEN_SEMCONV_GENAI", raising=False)
    monkeypatch.setattr(weaver, "_download", lambda url: b"not the archive")
    with pytest.raises(weaver.WeaverError, match="checksum mismatch.*LOGWARDEN_SEMCONV_GENAI"):
        weaver.genai_model("0.1.0", log=lambda m: None)
    assert not any(tmp_path.iterdir())
    with pytest.raises(weaver.WeaverError, match="no pinned checksum"):
        weaver.genai_model("0.0.1")
    monkeypatch.setenv("LOGWARDEN_SEMCONV_GENAI", str(tmp_path))
    assert weaver.genai_model("0.1.0") == tmp_path
    monkeypatch.setenv("LOGWARDEN_SEMCONV_GENAI", str(tmp_path / "nope"))
    with pytest.raises(weaver.WeaverError, match="not a directory"):
        weaver.genai_model("0.1.0")


def test_claim9_genai_download_and_cache(tmp_path, monkeypatch):
    import io
    import tarfile
    monkeypatch.setenv("LOGWARDEN_CACHE", str(tmp_path))
    monkeypatch.delenv("LOGWARDEN_SEMCONV_GENAI", raising=False)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        body = b"groups: []"
        info = tarfile.TarInfo("semantic-conventions-genai-9.9.9/model/gen-ai/registry.yaml")
        info.size = len(body)
        t.addfile(info, io.BytesIO(body))
    blob = buf.getvalue()
    monkeypatch.setitem(weaver.GENAI_SHA256, "9.9.9", hashlib.sha256(blob).hexdigest())
    monkeypatch.setattr(weaver, "_download", lambda url: blob)
    model = weaver.genai_model("9.9.9", log=lambda m: None)
    assert model == tmp_path / "semconv-genai-9.9.9/semantic-conventions-genai-9.9.9/model"
    monkeypatch.setattr(weaver, "_download", lambda url: (_ for _ in ()).throw(OSError("offline")))
    assert weaver.genai_model("9.9.9") == model
