#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Capture a sanitized, pagination-aware tools/list snapshot from DDAE or DDSE.

Credentials are read only from environment variables and are never accepted as
arguments or written to the snapshot. The script performs MCP initialization
and tool discovery only; it never calls a product tool or reads customer data.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import socket
import ssl
import stat
import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3 import PoolManager

ENGINES = {
    "ddae": {
        "url_env": "DDAE_MCP_URL",
        "authorization_env": "DDAE_OAUTH_TOKEN",
        "authorization_scheme": "Bearer",
        "protocol": "2025-03-26",
    },
    "ddse": {
        "url_env": "DDSE_MCP_URL",
        "authorization_env": "DDSE_MCP_AUTHORIZATION",
        "authorization_scheme": None,
        "protocol": "2025-06-18",
    },
}


class AuditError(RuntimeError):
    """Raised when safe contract capture cannot complete."""


class TLSContextAdapter(HTTPAdapter):
    """Use one explicit SSL context for direct and proxied HTTPS pools."""

    def __init__(self, context: ssl.SSLContext):
        self.context = context
        super().__init__()

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        pool_kwargs["ssl_context"] = self.context
        self.poolmanager = PoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            **pool_kwargs,
        )

    def proxy_manager_for(self, proxy, **proxy_kwargs):
        proxy_kwargs["ssl_context"] = self.context
        return super().proxy_manager_for(proxy, **proxy_kwargs)


def verified_ssl_context(
    ca_bundle: Path | None, *, legacy_ca_compat: bool
) -> ssl.SSLContext:
    """Build a verified context, optionally relaxing only X509 strict mode."""
    context = ssl.create_default_context(cafile=str(ca_bundle) if ca_bundle else None)
    strict = getattr(ssl, "VERIFY_X509_STRICT", 0)
    if legacy_ca_compat and strict:
        context.verify_flags &= ~strict
    return context


def parse_resolve(value: str | None, url: str) -> dict[str, str]:
    """Validate an optional endpoint-host override without weakening TLS."""
    if value is None:
        return {}
    try:
        hostname, address = value.rsplit("=", 1)
    except ValueError as exc:
        raise AuditError("--resolve must use HOST=IP") from exc
    expected_hostname = urlsplit(url).hostname
    if not hostname or hostname != expected_hostname:
        raise AuditError("--resolve host must exactly match the MCP URL hostname")
    try:
        ipaddress.ip_address(address)
    except ValueError as exc:
        raise AuditError("--resolve address must be an IP address") from exc
    return {hostname: address}


@contextmanager
def temporary_resolution(mapping: dict[str, str]):
    """Apply a process-local DNS override while preserving the URL hostname."""
    original = socket.getaddrinfo

    def getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        address = mapping.get(host, host)
        return original(address, port, family, type, proto, flags)

    socket.getaddrinfo = getaddrinfo
    try:
        yield
    finally:
        socket.getaddrinfo = original


def required_environment(engine: str, environ: dict[str, str]) -> tuple[str, str]:
    config = ENGINES[engine]
    url_name = config["url_env"]
    auth_name = config["authorization_env"]
    url = environ.get(url_name, "").strip()
    credential = environ.get(auth_name, "").strip()
    if not url:
        raise AuditError(f"{url_name} is required")
    if not url.startswith("https://"):
        raise AuditError(f"{url_name} must use HTTPS")
    if not credential:
        raise AuditError(f"{auth_name} is required")

    scheme = config["authorization_scheme"]
    authorization = f"{scheme} {credential}" if scheme else credential
    if engine == "ddse" and not authorization.startswith("ApiKey "):
        raise AuditError("DDSE_MCP_AUTHORIZATION must start with 'ApiKey '")
    return url, authorization


def parse_response(response: requests.Response) -> dict[str, Any] | None:
    if not response.content:
        return None
    content_type = response.headers.get("content-type", "").lower()
    if "application/json" in content_type:
        value = response.json()
        return value if isinstance(value, dict) else None
    if "text/event-stream" in content_type:
        events = [
            json.loads(line[5:].strip())
            for line in response.text.splitlines()
            if line.startswith("data:") and line[5:].strip()
        ]
        value = events[-1] if events else None
        return value if isinstance(value, dict) else None
    raise AuditError(f"unsupported MCP content type: {content_type or '<missing>'}")


def post(
    session: requests.Session,
    url: str,
    payload: dict[str, Any],
    *,
    session_id: str | None,
    timeout: float,
    verify: bool | str,
) -> tuple[requests.Response, dict[str, Any] | None]:
    headers = {"Mcp-Session-Id": session_id} if session_id else None
    response = session.post(
        url,
        headers=headers,
        json=payload,
        timeout=timeout,
        verify=verify,
    )
    body = parse_response(response)
    if response.status_code >= 400:
        raise AuditError(f"MCP request returned HTTP {response.status_code}")
    if isinstance(body, dict) and body.get("error"):
        error = body["error"]
        raise AuditError(
            f"MCP error {error.get('code', '<unknown>')}; response details omitted"
        )
    return response, body


def capture(
    engine: str,
    environ: dict[str, str],
    *,
    timeout: float,
    ca_bundle: Path | None,
    resolve: str | None = None,
    legacy_ca_compat: bool = False,
) -> dict[str, Any]:
    url, authorization = required_environment(engine, environ)
    resolution = parse_resolve(resolve, url)
    config = ENGINES[engine]
    session = requests.Session()
    if legacy_ca_compat:
        session.mount(
            "https://",
            TLSContextAdapter(verified_ssl_context(ca_bundle, legacy_ca_compat=True)),
        )
    session.headers.update(
        {
            "Authorization": authorization,
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "kbn-xsrf": "true",
        }
    )
    verify: bool | str = str(ca_bundle) if ca_bundle else True
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": config["protocol"],
            "capabilities": {},
            "clientInfo": {"name": "dell-aidp-contract-audit", "version": "1.0"},
        },
    }
    with temporary_resolution(resolution):
        response, body = post(
            session,
            url,
            initialize,
            session_id=None,
            timeout=timeout,
            verify=verify,
        )
        if not body or not isinstance(body.get("result"), dict):
            raise AuditError("initialize returned no MCP result")
        session_id = response.headers.get("Mcp-Session-Id")
        post(
            session,
            url,
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            session_id=session_id,
            timeout=timeout,
            verify=verify,
        )

        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        pages = 0
        while True:
            pages += 1
            params = {"cursor": cursor} if cursor else {}
            _, listing = post(
                session,
                url,
                {
                    "jsonrpc": "2.0",
                    "id": pages + 1,
                    "method": "tools/list",
                    "params": params,
                },
                session_id=session_id,
                timeout=timeout,
                verify=verify,
            )
            result = listing.get("result", {}) if listing else {}
            page_tools = result.get("tools", [])
            if not isinstance(page_tools, list):
                raise AuditError("tools/list returned an invalid tools collection")
            tools.extend(tool for tool in page_tools if isinstance(tool, dict))
            cursor = result.get("nextCursor")
            if not cursor:
                break
            if pages >= 20:
                raise AuditError("tools/list exceeded the 20-page safety limit")

    initialized = body["result"]
    server_info = initialized.get("serverInfo", {})
    return {
        "engine": engine,
        "validated_at": datetime.now(UTC).isoformat(),
        "protocol_version": initialized.get("protocolVersion"),
        "server_info": {
            "name": server_info.get("name"),
            "version": server_info.get("version"),
        },
        "pages": pages,
        "tool_count": len(tools),
        "tools": tools,
    }


def write_snapshot(path: Path, snapshot: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if os.name == "posix":
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("engine", choices=sorted(ENGINES))
    parser.add_argument("output", type=Path)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--ca-bundle", type=Path)
    parser.add_argument(
        "--resolve",
        metavar="HOST=IP",
        help="process-local endpoint resolution override; TLS still uses HOST",
    )
    parser.add_argument(
        "--legacy-ca-compat",
        action="store_true",
        help=(
            "retain chain and hostname verification but disable OpenSSL X509 strict "
            "mode for a legacy private CA"
        ),
    )
    args = parser.parse_args()
    try:
        snapshot = capture(
            args.engine,
            dict(os.environ),
            timeout=args.timeout,
            ca_bundle=args.ca_bundle,
            resolve=args.resolve,
            legacy_ca_compat=args.legacy_ca_compat,
        )
        write_snapshot(args.output, snapshot)
    except (
        AuditError,
        OSError,
        requests.RequestException,
        json.JSONDecodeError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "engine": snapshot["engine"],
                "output": str(args.output),
                "pages": snapshot["pages"],
                "tool_count": snapshot["tool_count"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
