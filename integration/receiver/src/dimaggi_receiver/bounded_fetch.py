"""Bounded HTTPS GET. A subprocess deadline also bounds DNS resolution.

Only caller-reviewed HTTPS URLs are sent; redirects and ambient proxies refuse.
Credential values travel on stdin, never command-line arguments or result logs.
"""

import base64
import json
import subprocess
import sys
from urllib.parse import urlsplit

MAX_RESPONSE = 4 * 1024 * 1024


def https_get(url, *, timeout=20, limit=MAX_RESPONSE, token=None):
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.port not in (None, 443)
        or parts.fragment
    ):
        raise ValueError(
            "explicit HTTPS URL without credentials, fragment or custom port required"
        )
    if (
        type(timeout) not in (int, float)
        or not 0 < timeout <= 30
        or type(limit) is not int
        or not 1 <= limit <= MAX_RESPONSE
    ):
        raise ValueError("bounded fetch limits required")
    request = json.dumps(
        dict(url=url, timeout=timeout, limit=limit, token=token)
    ).encode()
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "dimaggi_receiver.bounded_fetch"],
            input=request,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout + 1,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("HTTPS collection deadline exceeded") from exc
    if proc.returncode or len(proc.stdout) > limit * 2 + 2048:
        raise ValueError("HTTPS collection refused")
    value = json.loads(proc.stdout)
    raw = base64.b64decode(value["body"], validate=True)
    if len(raw) > limit:
        raise ValueError("response limit exceeded")
    return raw, value["content_type"]


def _worker():
    import urllib.request

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    request = json.loads(sys.stdin.buffer.read(16385))
    headers = {
        "User-Agent": "DIMAGGI-read-only-evidence/0.1",
        "Accept-Encoding": "identity",
    }
    if request["token"] is not None:
        token = request["token"]
        if (
            type(token) is not str
            or not 0 < len(token) <= 8192
            or any(ord(c) < 33 or ord(c) > 126 for c in token)
        ):
            raise ValueError("invalid token")
        headers["Authorization"] = "Bearer " + token
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(
        urllib.request.Request(request["url"], headers=headers),
        timeout=request["timeout"],
    ) as response:
        if response.status != 200 or response.headers.get(
            "Content-Encoding", "identity"
        ) not in ("", "identity"):
            raise ValueError("unsupported HTTP response")
        raw = response.read(request["limit"] + 1)
        if not raw or len(raw) > request["limit"]:
            raise ValueError("empty or oversized response")
        print(
            json.dumps(
                dict(
                    body=base64.b64encode(raw).decode(),
                    content_type=response.headers.get_content_type(),
                )
            )
        )


if __name__ == "__main__":
    try:
        _worker()
    except Exception:
        raise SystemExit(2)
