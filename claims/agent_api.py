"""Thin client for AssemblyAI's Agents REST API.

The API key lives in this process and never reaches the browser: the page gets
60-second session tokens minted here instead. What gets published is built by
AgentProfile.to_agent_config(); this module only moves it over the wire.
"""

import json
import logging
from urllib import error, request

from django.conf import settings

log = logging.getLogger("claims")


class AgentApiError(RuntimeError):
    def __init__(self, label, status, body):
        super().__init__(f"{label} failed ({status}): {body}")
        self.status = status
        self.body = body


def api(path, method="GET", body=None, timeout=30, headers=None):
    """Call the Agents API with the account key."""
    if not settings.ASSEMBLYAI_API_KEY:
        raise AgentApiError(path, 0, "ASSEMBLYAI_API_KEY is not set")
    url = settings.ASSEMBLYAI_AGENTS_API.rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    req = request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {settings.ASSEMBLYAI_API_KEY}")
    req.add_header("Content-Type", "application/json")
    for name, value in (headers or {}).items():
        req.add_header(name, value)
    try:
        with request.urlopen(req, timeout=timeout) as res:
            text = res.read().decode()
    except error.HTTPError as exc:
        raise AgentApiError(f"{method} {path}", exc.code, exc.read().decode()) from exc
    except error.URLError as exc:
        raise AgentApiError(f"{method} {path}", 0, str(exc.reason)) from exc
    return json.loads(text) if text else {}


def mint_token(expires_in_seconds=60):
    """A short-lived token the browser uses to open the agent websocket."""
    return api(
        f"/token?product=voice_agent&expires_in_seconds={int(expires_in_seconds)}"
    )


def publish_agent(config, agent_id=""):
    """Create the agent, or update the one we already published.

    Returns (agent_id, created). A stored id that has since been deleted falls
    back to creating a fresh agent rather than dead-ending.
    """
    if agent_id:
        try:
            api(f"/agents/{agent_id}", method="PUT", body=config)
            return agent_id, False
        except AgentApiError as exc:
            if exc.status != 404:
                raise
            log.warning("Agent %s no longer exists, creating a new one", agent_id)
    # No id: reuse an agent of the same name if one is already there, so a
    # restarted deploy does not pile up duplicates.
    try:
        listing = api("/agents")
        for existing in listing.get("agents", []) or []:
            if existing.get("name") == config.get("name"):
                api(f"/agents/{existing['id']}", method="PUT", body=config)
                return existing["id"], False
    except AgentApiError as exc:
        log.warning("Could not list agents (%s); creating a new one", exc)
    created = api("/agents", method="POST", body=config)
    return created["id"], True


def redacted_agent(agent):
    """The stored agent, safe to show on a page: no header values, no llm keys."""
    copy = json.loads(json.dumps(agent))
    for tool in copy.get("tools", []) or []:
        for header in (tool.get("http") or {}).get("headers", []) or []:
            header["value"] = "<hidden>"
    for llm in copy.get("llm", []) or []:
        llm.pop("api_key", None)
    return copy
