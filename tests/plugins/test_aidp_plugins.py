import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PLUGINS = {
    "dell-data-analytics-engine": {
        "server": "dell_data_analytics_engine",
        "url_env": "DDAE_MCP_URL",
        "skill": "configure-dell-data-analytics-engine",
        "minimum_workflows": 6,
    },
    "dell-data-search-engine": {
        "server": "dell_data_search_engine",
        "url_env": "DDSE_MCP_URL",
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
        assert manifest["skills"] == "./skills/"
        assert manifest["mcpServers"] == "./.mcp.json"
        assert manifest["interface"]["capabilities"] == ["Read"]
        assert manifest["interface"]["shortDescription"]
        assert manifest["interface"]["longDescription"]
        assert len(manifest["interface"]["defaultPrompt"]) >= 2

        mcp = load_json(ROOT / name / ".mcp.json")["mcpServers"]
        server = mcp[expected["server"]]
        assert server["type"] == "http"
        assert server["url"] == f"${{env:{expected['url_env']}}}"
        serialized = json.dumps(mcp)
        assert "lab9bgp" not in serialized
        assert "ApiKey " not in serialized


def test_authentication_is_environment_sourced():
    ddae = load_json(ROOT / "dell-data-analytics-engine/.mcp.json")
    ddae_server = ddae["mcpServers"]["dell_data_analytics_engine"]
    assert ddae_server["oauth_resource"] == "${env:DDAE_OAUTH_RESOURCE}"

    ddse = load_json(ROOT / "dell-data-search-engine/.mcp.json")
    ddse_server = ddse["mcpServers"]["dell_data_search_engine"]
    assert ddse_server["env_http_headers"] == {
        "Authorization": "DDSE_MCP_AUTHORIZATION"
    }
    assert "headers" not in ddse_server


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
