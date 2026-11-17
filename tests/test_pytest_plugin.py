"""v0.3.0 claim 6: the capture plugin is registered by the logwarden package (pytest11 entry point)."""
import json


def test_plugin_registered(pytester):
    pytester.makepyfile("def test_x(pytestconfig):\n"
                        "    assert pytestconfig.pluginmanager.has_plugin('logwarden')\n")
    pytester.runpytest_subprocess().assert_outcomes(passed=1)


def test_capture(pytester, monkeypatch, tmp_path):
    out = tmp_path / "logs.jsonl"
    monkeypatch.setenv("LOGWARDEN_CAPTURE", str(out))
    pytester.makepyfile(
        "import json, logging\n"
        "def test_emits():\n"
        "    logging.getLogger('app').info(json.dumps({'event': 'thing_done'}))\n"
        "    logging.getLogger('app').debug(json.dumps({'event': 'too_low'}))\n")
    pytester.runpytest_subprocess().assert_outcomes(passed=1)
    lines = [json.loads(x) for x in out.read_text().splitlines()]
    assert lines == [{"event": "thing_done"}]


def test_no_capture_without_env(pytester, monkeypatch):
    monkeypatch.delenv("LOGWARDEN_CAPTURE", raising=False)
    pytester.makepyfile("import logging\ndef test_x():\n    logging.getLogger().info('x')\n")
    pytester.runpytest_subprocess().assert_outcomes(passed=1)
