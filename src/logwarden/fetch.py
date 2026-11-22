"""Fetch recent logs from Elasticsearch, and push metrics to a Prometheus Pushgateway.

For scheduled checks of production logs: `logwarden fetch` writes a `_search` response that
`logwarden check` reads, and `check --pushgateway` sends the per-service metrics. Credentials
come from the environment, never from the command line (it shows up in process lists and CI logs).
"""
from __future__ import annotations

import base64
import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request

MAX_SIZE = 10_000            # Elasticsearch's default index.max_result_window
_SINCE = re.compile(r"^[1-9][0-9]*[smhd]$")


class FetchError(RuntimeError):
    pass


def auth_header(value: str | None) -> dict[str, str]:
    """LOGWARDEN_ES_AUTH: 'ApiKey …', 'Basic …', 'Bearer …', or 'user:password'."""
    value = (value or "").strip()
    if not value:
        return {}
    if value.split(" ", 1)[0] in ("ApiKey", "Basic", "Bearer"):
        return {"Authorization": value}
    if ":" in value:
        return {"Authorization": "Basic " + base64.b64encode(value.encode("utf-8")).decode("ascii")}
    return {"Authorization": "ApiKey " + value}


def _context(ca: str | None, insecure: bool) -> ssl.SSLContext | None:
    if insecure:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    if ca:
        try:
            return ssl.create_default_context(cafile=ca)
        except (OSError, ssl.SSLError) as e:
            raise FetchError(f"can't read CA file {ca}: {e}") from e
    return None


def query(since: str, size: int, time_field: str, query_string: str | None) -> dict:
    flt: list[dict] = [{"range": {time_field: {"gte": f"now-{since}"}}}]
    if query_string:
        flt.append({"query_string": {"query": query_string}})
    return {"size": size, "track_total_hits": True,
            "sort": [{time_field: {"order": "desc", "unmapped_type": "date"}}],
            "query": {"bool": {"filter": flt}}}


def es(url: str, index: str, *, since: str = "1h", size: int = MAX_SIZE, time_field: str = "@timestamp",
       query_string: str | None = None, auth: str | None = None, ca: str | None = None,
       insecure: bool = False, timeout: float = 60) -> tuple[bytes, int, int]:
    """The newest `size` hits of the last `since` in `index`: (response, returned, matched)."""
    if not _SINCE.match(since):
        raise FetchError(f"--since must be a number and a unit (s, m, h, d), e.g. 1h or 30m, not '{since}'")
    if not 1 <= size <= MAX_SIZE:
        raise FetchError(f"--size must be between 1 and {MAX_SIZE}")
    if not url.startswith(("http://", "https://")):
        raise FetchError(f"Elasticsearch URL must start with http:// or https://, not '{url}'")
    target = f"{url.rstrip('/')}/{urllib.parse.quote(index, safe='*,-_.')}/_search"
    body = json.dumps(query(since, size, time_field, query_string)).encode("utf-8")
    req = urllib.request.Request(target, data=body, method="POST",
                                 headers={"Content-Type": "application/json", **auth_header(auth)})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_context(ca, insecure)) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        reason = {401: "the credentials were refused (set LOGWARDEN_ES_AUTH)",
                  403: "the credentials can't read this index",
                  404: f"no index matches '{index}'"}.get(e.code, f"HTTP {e.code}")
        raise FetchError(f"Elasticsearch at {url}: {reason}") from e
    except (urllib.error.URLError, OSError) as e:
        raise FetchError(f"can't reach Elasticsearch at {url}: {getattr(e, 'reason', e)}") from e
    try:
        doc = json.loads(raw)
        hits = doc["hits"]["hits"]
        total = doc["hits"].get("total")
        matched = total.get("value") if isinstance(total, dict) else total
    except (ValueError, KeyError, TypeError) as e:
        raise FetchError(f"Elasticsearch at {url} didn't return a _search response") from e
    if not hits:
        raise FetchError(f"no log lines in the last {since} of '{index}'"
                         + (f" matching '{query_string}'" if query_string else "")
                         + f" (time field {time_field})")
    return raw, len(hits), matched if isinstance(matched, int) else len(hits)


def push(url: str, metrics_text: str, timeout: float = 30) -> str:
    """PUT the metrics to a Pushgateway, replacing the group. Returns the URL used."""
    if not url.startswith(("http://", "https://")):
        raise FetchError(f"Pushgateway URL must start with http:// or https://, not '{url}'")
    if "/metrics/job/" not in url:
        url = url.rstrip("/") + "/metrics/job/logwarden"
    headers = {"Content-Type": "text/plain; version=0.0.4"}
    if os.environ.get("LOGWARDEN_PUSHGATEWAY_AUTH"):
        headers.update(auth_header(os.environ["LOGWARDEN_PUSHGATEWAY_AUTH"]))
    req = urllib.request.Request(url, data=metrics_text.encode("utf-8"), method="PUT", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout):
            pass
    except urllib.error.HTTPError as e:
        body = e.read()[:300].decode("utf-8", "replace").strip()
        raise FetchError(f"Pushgateway at {url}: HTTP {e.code} {body}") from e
    except (urllib.error.URLError, OSError) as e:
        raise FetchError(f"can't reach the Pushgateway at {url}: {getattr(e, 'reason', e)}") from e
    return url
