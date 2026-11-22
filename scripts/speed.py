"""Time `logwarden check --profile essential` on generated log lines (CI job `speed`).

    python scripts/speed.py 100000 120   # lines, and the seconds it may take at most
"""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

n, limit = int(sys.argv[1]), float(sys.argv[2])
with tempfile.TemporaryDirectory() as tmp:
    logs = Path(tmp) / "logs.jsonl"
    with logs.open("w", encoding="utf-8") as f:
        for i in range(n):   # varied lines: every attribute goes through the sensitive-data rules
            f.write(json.dumps({"timestamp": "2026-11-22T10:00:00Z", "level": "warning" if i % 10 == 0 else "info",
                                "event": "order_created", "logger": "shop.orders", "service.name": "orders",
                                "service.version": "1.0.0", "deployment.environment.name": "production",
                                "order.id": f"ord-{i:08d}", "http.route": "/orders/{id}",
                                "http.response.status_code": 200, "duration_ms": i % 1000 / 7}) + "\n")
    start = time.monotonic()
    p = subprocess.run([sys.executable, "-m", "logwarden", "check", str(logs), "--profile", "essential", "--json"],
                       capture_output=True, text=True)
    took = time.monotonic() - start
result = json.loads(p.stdout)
print(f"{n:,} lines in {took:.1f} s ({took / n * 1000:.2f} ms per line), exit {p.returncode}, "
      f"{result['records']:,} records")
assert result["records"] == n, result
sys.exit(0 if took <= limit else f"slower than {limit:.0f} s")
