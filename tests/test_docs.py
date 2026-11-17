"""Docs that copy rule data stay in sync with the rules."""
import re

from conftest import ROOT
from logwarden import data


def test_sloglint_forbidden_keys_match_banned_synonyms():
    rego = data.path("policies/logwarden.rego").read_text(encoding="utf-8")
    banned = set(re.findall(r'^\t"([^"]+)":', rego.split("banned := {", 1)[1].split("}", 1)[0], re.M))
    doc = (ROOT / "docs/integration.md").read_text(encoding="utf-8")
    listed = re.search(r"forbidden-keys: \[([^\]]+)\]", doc).group(1)
    assert {k.strip() for k in listed.split(",")} == banned
