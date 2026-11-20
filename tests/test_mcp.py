"""v0.3.0 claim 7: `logwarden mcp` works with the [mcp] extra and fails clearly without it."""
import asyncio
import json
import os
import subprocess
import sys

import pytest

from conftest import EXAMPLES

BAD = ('{"level":"error","event":"Sync failed for 3","logger":"a","status":500,"service.name":"s",'
       '"service.version":"1","deployment.environment.name":"prod"}')


def test_clear_error_without_extra(tmp_path):
    code = ("import sys; sys.modules['mcp'] = None\n"
            "from logwarden.cli import main\n"
            "sys.exit(main(['mcp']))\n")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 2
    assert "logwarden[mcp]" in r.stderr


@pytest.mark.weaver
def test_stdio_tools(tmp_path):
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    def payload(res):
        return res.structured_content or json.loads(res.content[0].text)

    async def go():
        params = StdioServerParameters(command=sys.executable, args=["-m", "logwarden", "mcp"],
                                       cwd=str(tmp_path), env=dict(os.environ))   # keeps coverage on
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            names = {t.name for t in (await s.list_tools()).tools}
            assert names == {"check_logs", "list_rules", "explain_rule", "get_standard"}

            single = payload(await s.call_tool("check_logs", {"lines": BAD}))
            assert single["ok"] is False
            assert {f["rule"] for f in single["findings"]} >= {"adr_banned_synonym", "adr_event_name"}

            multi = payload(await s.call_tool("check_logs", {"lines": BAD + "\nplain\n" + BAD}))
            assert (multi["records"], multi["skipped_lines"]) == (2, 1)

            good = payload(await s.call_tool("check_logs", {"path": str(EXAMPLES / "good.jsonl")}))
            assert good["ok"] is True

            assert "error" in payload(await s.call_tool("check_logs", {}))

            # v0.4.0 claim 10: the project's disable/baseline apply to check_logs.
            (tmp_path / "logwarden.toml").write_text(
                'disable = { adr_event_name = "legacy" }\nbaseline = "bl.json"\n')
            assert "not found" in payload(await s.call_tool("check_logs", {"lines": BAD}))["error"]
            (tmp_path / "logs.jsonl").write_text(BAD + "\n")
            assert subprocess.run([sys.executable, "-m", "logwarden", "baseline", "logs.jsonl",
                                   "--output", "bl.json"], cwd=tmp_path).returncode == 0
            res = payload(await s.call_tool("check_logs", {"path": str(tmp_path / "logs.jsonl")}))
            assert res["ok"] is True and res["baselined"] > 0
            assert res["disabled"][0]["rule"] == "adr_event_name"
            text = (await s.call_tool("explain_rule", {"rule_id": "adr_failure_context"})).content[0].text
            assert text.startswith("## adr_failure_context")
            std = (await s.call_tool("get_standard", {})).content[0].text
            assert std.startswith("# Logging standard")

    asyncio.run(go())
