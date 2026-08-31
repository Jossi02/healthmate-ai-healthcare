#!/usr/bin/env python3
"""Non-destructive HTTPS smoke check for an already deployed Backend."""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

MAX_RESPONSE_BYTES = 64 * 1024


class RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("deployment smoke does not follow redirects")


def validate_base_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("base URL must use HTTPS and include a hostname")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("base URL must not contain credentials, a query, or a fragment")
    if parsed.path not in ("", "/"):
        raise ValueError("base URL must be an origin without a path")
    return urlunsplit(("https", parsed.netloc, "", "", ""))


def get_json(base_url: str, path: str, timeout: float, opener) -> dict:
    request = Request(f"{base_url}{path}", headers={"Accept": "application/json"})
    with opener.open(request, timeout=timeout) as response:
        body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError(f"{path} response exceeds {MAX_RESPONSE_BYTES} bytes")
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} did not return a JSON object")
    return payload


def run(base_url: str, timeout: float) -> None:
    context = ssl.create_default_context()
    opener = build_opener(HTTPSHandler(context=context), RejectRedirects())
    health = get_json(base_url, "/api/health", timeout, opener)
    readiness = get_json(base_url, "/api/readiness", timeout, opener)
    if health.get("success") is not True:
        raise ValueError("health response did not report success")
    if readiness.get("ok") is not True:
        raise ValueError("readiness response did not report ready")
    print(json.dumps({"ok": True, "tls_verified": True, "checks": ["health", "readiness"]}))


def self_test() -> None:
    assert validate_base_url("https://api.example.com/") == "https://api.example.com"
    for invalid in (
        "http://api.example.com",
        "https://user@api.example.com",
        "https://api.example.com/path",
        "https://api.example.com?token=value",
    ):
        try:
            validate_base_url(invalid)
        except ValueError:
            continue
        raise AssertionError(f"unsafe URL accepted: {invalid}")
    try:
        RejectRedirects().redirect_request(None, None, 302, "Found", {}, "http://example.com")
    except ValueError:
        pass
    else:
        raise AssertionError("redirect accepted")
    print(json.dumps({"ok": True, "self_test": True}))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("HEALTHMATE_BASE_URL"))
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return 0
    if not args.base_url:
        parser.error("--base-url or HEALTHMATE_BASE_URL is required")
    if args.timeout <= 0 or args.timeout > 60:
        parser.error("--timeout must be greater than 0 and no more than 60 seconds")

    run(validate_base_url(args.base_url), args.timeout)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (HTTPError, URLError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        print(f"Deployment smoke failed: {error}", file=sys.stderr)
        raise SystemExit(1)
