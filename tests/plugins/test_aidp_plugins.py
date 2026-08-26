import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PLUGINS = {
    "dell-data-analytics-engine": {
        "skill": "configure-dell-data-analytics-engine",
        "minimum_workflows": 6,
    },
    "dell-data-search-engine": {
        "skill": "configure-dell-data-search-engine",
        "minimum_workflows": 8,
    },
}


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_marketplace_contains_aidp_plugins_with_governance_policy():
    marketplace = load_json(ROOT / ".agents/plugins/marketplace.json")
    entries = {entry["name"]: entry for entry in marketplace["plugins"]}
    for name in PLUGINS:
        entry = entries[name]
        assert entry["source"] == {"source": "local", "path": f"./{name}"}
        assert entry["policy"] == {
            "installation": "AVAILABLE",
            "authentication": "ON_INSTALL",
        }
        assert entry["category"]


def test_aidp_manifests_are_focused_read_only_packages():
    for name, expected in PLUGINS.items():
        manifest = load_json(ROOT / name / ".codex-plugin/plugin.json")
        assert manifest["name"] == name
        assert "+codex." not in manifest["version"]
        assert manifest["skills"] == "./skills/"
        assert "mcpServers" not in manifest
        assert not (ROOT / name / ".mcp.json").exists()
        assert manifest["interface"]["capabilities"] == ["Read"]
        assert manifest["interface"]["shortDescription"]
        assert manifest["interface"]["longDescription"]
        assert len(manifest["interface"]["defaultPrompt"]) >= 2

        serialized = json.dumps(manifest)
        assert "lab9bgp" not in serialized
        assert "ApiKey " not in serialized


def test_customer_mcp_registration_is_documented_without_bundled_endpoint():
    ddae = (ROOT / "dell-data-analytics-engine/skills/configure-dell-data-analytics-engine/SKILL.md").read_text(encoding="utf-8")
    assert "codex mcp add dell_data_analytics_engine" in ddae
    assert '--url "$DDAE_MCP_URL"' in ddae
    assert "--oauth-resource" not in ddae
    assert "protected-resource discovery" in ddae
    assert "exactly one resource" in ddae

    ddse = (ROOT / "dell-data-search-engine/skills/configure-dell-data-search-engine/SKILL.md").read_text(encoding="utf-8")
    assert "[mcp_servers.dell_data_search_engine]" in ddse
    assert 'env_http_headers = { Authorization = "DDSE_MCP_AUTHORIZATION" }' in ddse
    assert 'export DDSE_MCP_AUTHORIZATION="ApiKey <encoded-api-key>"' in ddse

    assert "lab9bgp" not in ddae
    assert "lab9bgp" not in ddse


def test_ddae_oauth_guides_separate_user_and_admin_responsibilities():
    reference_dir = (
        ROOT
        / "dell-data-analytics-engine/skills/configure-dell-data-analytics-engine/references"
    )
    user_guide = (reference_dir / "DDAE_OAUTH_USER_GUIDE.md").read_text(
        encoding="utf-8"
    )
    admin_guide = (reference_dir / "DDAE_OAUTH_ADMIN_GUIDE.md").read_text(
        encoding="utf-8"
    )

    for phrase in (
        "codex mcp add dell_data_analytics_engine",
        "codex mcp login dell_data_analytics_engine --scopes openid",
        "Verify the connection",
        "Information to give an administrator",
        "SELECT 1 AS validation_value LIMIT 1",
    ):
        assert phrase in user_guide

    for phrase in (
        "token_endpoint_auth_method",
        '"response_types": ["code"]',
        "PKCE S256",
        "Allowed Client Scopes",
        "Trusted Hosts",
        "default client scope",
        "preferred_username",
        "pre-registered fallback client",
        "duplicated parameter",
        "SELECT 1 AS validation_value LIMIT 1",
    ):
        assert phrase in admin_guide

    assert "Trusted Hosts" not in user_guide
    assert "codex mcp add dell_data_analytics_engine" not in admin_guide
    assert not (reference_dir / "DDAE_OAUTH_USAGE_GUIDE.md").exists()
    for guide in (user_guide, admin_guide):
        assert "lab9bgp" not in guide
        assert "SOCKS" not in guide
        assert "SSH tunnel" not in guide


def test_skills_require_live_catalog_authorization_and_bounded_reads():
    for name, expected in PLUGINS.items():
        skill_dir = ROOT / name / "skills" / expected["skill"]
        text = (skill_dir / "SKILL.md").read_text(encoding="utf-8").lower()
        for phrase in (
            "tools/list",
            "authorization",
            "bounded",
            "evaluation-plan.md",
            "synthetic fixtures",
        ):
            assert phrase in text


def test_evaluation_plans_cover_workshop_case_fields_and_variants():
    for name, expected in PLUGINS.items():
        path = (
            ROOT
            / name
            / "skills"
            / expected["skill"]
            / "references/evaluation-plan.md"
        )
        text = path.read_text(encoding="utf-8")
        lower = text.lower()
        assert text.count("\n| ") >= expected["minimum_workflows"] + 1
        for phrase in (
            "success case",
            "edge case",
            "safety case",
            "expected tool path",
            "exact user prompt",
            "fixture",
            "verifier",
            "tool calls",
            "pass/fail",
            "latency",
            "transcript link",
            "five reproducible positive cases",
            "three negative cases",
        ):
            assert phrase in lower
