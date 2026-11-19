"""Every place that names the version agrees (package, plugin, marketplace, MCP pin)."""
import json

from conftest import ROOT
from logwarden import __version__


def test_versions_agree():
    plugin = json.loads((ROOT / "plugin/.claude-plugin/plugin.json").read_text())
    market = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())
    mcp = json.loads((ROOT / "plugin/.mcp.json").read_text())
    assert plugin["version"] == __version__
    assert market["plugins"][0]["version"] == __version__
    assert f"logwarden[mcp]=={__version__}" in mcp["mcpServers"]["logwarden"]["args"]


def test_docs_name_this_version():
    """@vX.Y.Z (Action), ==X.Y.Z (uvx) and :X.Y.Z (Docker image) references point at this release."""
    import re
    for doc in ("README.md", "docs/integration.md"):
        text = (ROOT / doc).read_text(encoding="utf-8")
        refs = re.findall(r"logwarden@v([0-9.]+)|logwarden==([0-9.]+)|logwarden:([0-9.]+)", text)
        found = {v for ref in refs for v in ref if v}
        assert found == {__version__}, (doc, found)
