import importlib.util
import json
import os
import stat
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "capture_remote_mcp_contract.py"


def load_module():
    spec = importlib.util.spec_from_file_location("capture_remote_mcp_contract", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_credentials_are_environment_only_and_schemes_are_enforced():
    module = load_module()
    assert module.required_environment(
        "ddae",
        {"DDAE_MCP_URL": "https://example.test/mcp", "DDAE_OAUTH_TOKEN": "secret"},
    ) == ("https://example.test/mcp", "Bearer secret")
    assert module.required_environment(
        "ddse",
        {
            "DDSE_MCP_URL": "https://example.test/mcp",
            "DDSE_MCP_AUTHORIZATION": "ApiKey secret",
        },
    ) == ("https://example.test/mcp", "ApiKey secret")

    with pytest.raises(module.AuditError, match="must start"):
        module.required_environment(
            "ddse",
            {
                "DDSE_MCP_URL": "https://example.test/mcp",
                "DDSE_MCP_AUTHORIZATION": "Bearer wrong-scheme",
            },
        )


def test_snapshot_writer_is_private_and_contains_no_credential(tmp_path):
    module = load_module()
    path = tmp_path / "contract.json"
    snapshot = {
        "engine": "ddae",
        "tool_count": 1,
        "tools": [{"name": "searchDataProducts", "inputSchema": {"type": "object"}}],
    }
    module.write_snapshot(path, snapshot)

    assert json.loads(path.read_text(encoding="utf-8")) == snapshot
    assert "secret" not in path.read_text(encoding="utf-8")
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_script_help_does_not_accept_credentials_as_arguments():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "--token" not in text
    assert "--authorization" not in text
    assert '"tools/list"' in text
    assert '"tools/call"' not in text


def test_resolve_override_must_match_url_host_and_use_an_ip():
    module = load_module()
    url = "https://kibana.example.test/api/agent_builder/mcp"

    assert module.parse_resolve("kibana.example.test=192.0.2.10", url) == {
        "kibana.example.test": "192.0.2.10"
    }
    with pytest.raises(module.AuditError, match="exactly match"):
        module.parse_resolve("other.example.test=192.0.2.10", url)
    with pytest.raises(module.AuditError, match="IP address"):
        module.parse_resolve("kibana.example.test=not-an-ip", url)


def test_temporary_resolution_is_process_local_and_restored(monkeypatch):
    module = load_module()
    calls = []

    def fake_getaddrinfo(host, *args):
        calls.append(host)
        return [(host, args)]

    monkeypatch.setattr(module.socket, "getaddrinfo", fake_getaddrinfo)
    with module.temporary_resolution({"kibana.example.test": "192.0.2.10"}):
        module.socket.getaddrinfo("kibana.example.test", 443)
    module.socket.getaddrinfo("kibana.example.test", 443)

    assert calls == ["192.0.2.10", "kibana.example.test"]


def test_legacy_ca_compat_keeps_verification_enabled():
    module = load_module()
    context = module.verified_ssl_context(None, legacy_ca_compat=True)

    assert context.check_hostname is True
    assert context.verify_mode == module.ssl.CERT_REQUIRED
    strict = getattr(module.ssl, "VERIFY_X509_STRICT", 0)
    if strict:
        assert context.verify_flags & strict == 0
