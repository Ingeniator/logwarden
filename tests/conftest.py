import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
DATA = Path(__file__).resolve().parent / "data"
GOLDEN = Path(__file__).resolve().parent / "golden"


def run_cli(*args, stdin=None, cwd=None, env=None):
    """Run `python -m logwarden ...` as a user would; returns CompletedProcess."""
    e = {**os.environ, **(env or {})}
    return subprocess.run(
        [sys.executable, "-m", "logwarden", *map(str, args)],
        input=stdin, capture_output=True, text=True, encoding="utf-8", cwd=cwd, env=e,
    )


@pytest.fixture
def cli():
    return run_cli
