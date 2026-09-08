"""Reachability of the public base URL that AssemblyAI will POST tools to."""

import urllib.error
import urllib.request
from urllib.parse import urlparse


def probe_webhook(base_url, request_host="", timeout=2.0):
    """HEAD/GET the site. Never POST a fake claim.

    Same-host URLs are treated as reachable without a network hop — this
    process is already serving them. Remote URLs get a short GET of /healthz/.
    """
    url = (base_url or "").rstrip("/")
    if not url:
        return {"state": "unset", "label": "Webhook not set"}

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return {"state": "bad", "label": "Webhook URL invalid"}

    host = (request_host or "").split(":")[0].lower()
    target = parsed.hostname.lower() if parsed.hostname else ""
    if host and target and host == target:
        return {"state": "ok", "label": "Webhook reachable"}

    probe = url + "/healthz/"
    req = urllib.request.Request(probe, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if 200 <= resp.status < 500:
                return {"state": "ok", "label": "Webhook reachable"}
            return {"state": "bad", "label": "Webhook unreachable"}
    except urllib.error.HTTPError as exc:
        if 400 <= exc.code < 500:
            return {"state": "ok", "label": "Webhook reachable"}
        return {"state": "bad", "label": "Webhook unreachable"}
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return {"state": "bad", "label": "Webhook unreachable"}
