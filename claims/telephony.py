"""Twilio's REST API, which is form-encoded with basic auth and needs no SDK.

`connect_phone` builds the SIP trunk with this; the transfer uses it to take
hold of a call that is already in progress. Kept in one place because there is
exactly one way to talk to Twilio and two callers who need it.
"""

import base64
import json
import logging
import os
import re
import urllib.parse
from urllib import error, request

log = logging.getLogger("claims")

API = "https://api.twilio.com/2010-04-01"


class TelephonyError(RuntimeError):
    def __init__(self, label, status, body):
        super().__init__(f"Twilio {label} failed ({status}): {body}")
        self.status = status
        self.body = body


def credentials():
    return (
        os.environ.get("TWILIO_ACCOUNT_SID", ""),
        os.environ.get("TWILIO_AUTH_TOKEN", ""),
    )


def configured():
    sid, token = credentials()
    return bool(sid and token)


def call(url, form=None, method=None):
    """GET, or POST when there is a form. Returns parsed JSON, or {}."""
    sid, token = credentials()
    auth = base64.b64encode(f"{sid}:{token}".encode()).decode()
    data = urllib.parse.urlencode(form).encode() if form else None
    req = request.Request(url, data=data, method=method or ("POST" if form else "GET"))
    req.add_header("Authorization", f"Basic {auth}")
    if form:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with request.urlopen(req, timeout=20) as res:
            body = res.read().decode()
    except error.HTTPError as exc:
        label = re.sub(r"https://[^/]+", "", url).split("?")[0]
        raise TelephonyError(label, exc.code, exc.read().decode()) from exc
    except error.URLError as exc:
        label = re.sub(r"https://[^/]+", "", url).split("?")[0]
        raise TelephonyError(label, 0, str(exc.reason)) from exc
    return json.loads(body) if body else {}


def account(path, form=None, method=None):
    """A path under this account, like `/Calls.json`."""
    sid, _ = credentials()
    return call(f"{API}/Accounts/{sid}{path}", form=form, method=method)
