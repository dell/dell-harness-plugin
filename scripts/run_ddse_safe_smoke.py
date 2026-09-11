#!/usr/bin/env python3
"""Run non-sensitive, read-only smoke checks against the DDSE MCP endpoint.

The checks use only synthetic nonexistent names and an index-free constant
ES|QL expression. Raw tool content is never printed or written.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import stat
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests

ROOT = Path(__file__).resolve().parents[1]
CAPTURE_SCRIPT = ROOT / "scripts" / "capture_remote_mcp_contract.py"


def load_capture_module():
    spec = importlib.util.spec_from_file_location(
        "capture_remote_mcp_contract", CAPTURE_SCRIPT
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load the MCP transport helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def summarize_tool_result(
    case_id: str, tool: str, body: dict[str, Any], elapsed_ms: int
) -> dict[str, Any]:
    result = body.get("result")
    if not isinstance(result, dict):
        return {
            "case_id": case_id,
            "tool": tool,
            "outcome": "protocol_error",
            "latency_ms": elapsed_ms,
        }
    content = result.get("content")
    items = content if isinstance(content, list) else []
    return {
        "case_id": case_id,
        "tool": tool,
        "outcome": "tool_error" if result.get("isError") is True else "success",
        "latency_ms": elapsed_ms,
        "content_items": len(items),
        "content_types": sorted(
            {
                item.get("type")
                for item in items
                if isinstance(item, dict) and isinstance(item.get("type"), str)
            }
        ),
        "structured_content_present": isinstance(result.get("structuredContent"), dict),
    }


def run_smoke(
    environ: dict[str, str],
    *,
    timeout: float,
    ca_bundle: Path | None,
    resolve: str | None,
    proxy: str | None,
    legacy_ca_compat: bool,
) -> dict[str, Any]:
    capture = load_capture_module()
    url, authorization = capture.required_environment("ddse", environ)
    resolution = capture.parse_resolve(resolve, url)
    session = requests.Session()
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
    if legacy_ca_compat:
        session.mount(
            "https://",
            capture.TLSContextAdapter(
                capture.verified_ssl_context(ca_bundle, legacy_ca_compat=True)
            ),
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
    protocol = capture.ENGINES["ddse"]["protocol"]
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": protocol,
            "capabilities": {},
            "clientInfo": {"name": "dell-ddse-safe-smoke", "version": "1.0"},
        },
    }
    safe_cases = (
        (
            "synthetic-no-match-discovery",
            "platform_core_list_indices",
            {"pattern": "openai-review-no-match-20260910-*"},
        ),
        (
            "synthetic-missing-index-mapping",
            "platform_core_get_index_mapping",
            {"indices": ["openai-review-nonexistent-20260910"]},
        ),
        (
            "index-free-esql",
            "platform_core_execute_esql",
            {"query": "ROW validation_value = 1 | LIMIT 1"},
        ),
        (
            "required-argument-enforcement",
            "platform_core_get_index_mapping",
            {},
        ),
    )
    results: list[dict[str, Any]] = []
    with capture.temporary_resolution(resolution):
        response, body = capture.post(
            session,
            url,
            initialize,
            session_id=None,
            timeout=timeout,
            verify=verify,
        )
        if not body or not isinstance(body.get("result"), dict):
            raise capture.AuditError("initialize returned no MCP result")
        session_id = response.headers.get("Mcp-Session-Id")
        capture.post(
            session,
            url,
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            session_id=session_id,
            timeout=timeout,
            verify=verify,
        )
        for request_id, (case_id, tool, arguments) in enumerate(safe_cases, start=2):
            started = time.monotonic()
            _, tool_body = capture.post(
                session,
                url,
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "tools/call",
                    "params": {"name": tool, "arguments": arguments},
                },
                session_id=session_id,
                timeout=timeout,
                verify=verify,
            )
            elapsed_ms = round((time.monotonic() - started) * 1000)
            results.append(
                summarize_tool_result(case_id, tool, tool_body or {}, elapsed_ms)
            )

    initialized = body["result"]
    server_info = initialized.get("serverInfo", {})
    return {
        "engine": "ddse",
        "validated_at": datetime.now(UTC).isoformat(),
        "endpoint_host": urlsplit(url).hostname,
        "protocol_version": initialized.get("protocolVersion"),
        "server_info": {
            "name": server_info.get("name"),
            "version": server_info.get("version"),
        },
        "safety_profile": "synthetic names and index-free constant query; raw content omitted",
        "checks": results,
    }


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if os.name == "posix":
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--ca-bundle", type=Path)
    parser.add_argument("--resolve", metavar="HOST=IP")
    parser.add_argument("--proxy", help="optional HTTP or SOCKS proxy URL")
    parser.add_argument("--legacy-ca-compat", action="store_true")
    args = parser.parse_args()
    try:
        report = run_smoke(
            dict(os.environ),
            timeout=args.timeout,
            ca_bundle=args.ca_bundle,
            resolve=args.resolve,
            proxy=args.proxy,
            legacy_ca_compat=args.legacy_ca_compat,
        )
        write_report(args.output, report)
    except (
        RuntimeError,
        OSError,
        requests.RequestException,
        json.JSONDecodeError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "engine": report["engine"],
                "output": str(args.output),
                "checks": [
                    {"case_id": item["case_id"], "outcome": item["outcome"]}
                    for item in report["checks"]
                ],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
