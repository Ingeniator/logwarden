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
