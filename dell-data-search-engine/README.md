# Dell Data Search Engine for Codex

This plugin supplies reusable Codex skills for the native Kibana Agent Builder
Streamable HTTP MCP endpoint in a customer-managed Dell Data Search Engine
(DDSE) deployment. The customer registers the MCP endpoint separately. The
package contains no endpoint, API key, Kibana space, certificate, or customer
data.

## Before you install

This plugin contains skills only. It needs a deployed Dell AI Data Platform
(AIDP) instance whose Data Search Engine release includes the Elastic Kibana
Agent Builder MCP server, and that MCP endpoint must be registered in Codex
before the skills can do anything. There is no public or demo endpoint. See the
[Dell AI Data Platform](https://www.dell.com/en-us/shop/artificial-intelligence/sc/ai-data-platform) documentation for the AIDP and Elastic
releases that support the MCP server.

## Customer capabilities

Depending on the Elastic release, license, Kibana space, installed content,
model configuration, and assigned privileges, customers can:

- Discover authorized indices and mappings and retrieve known documents.
- Execute exact, bounded ES|QL and investigate governed search data.
- Explore logs, traces, hosts, services, alerts, cases, and change points.
- Inspect integrations, workflows, and installed Elastic knowledge content.
- Use natural-language search and ES|QL generation when an Agent Builder chat
  model is configured in the same Kibana space.

The live `tools/list` response is authoritative. The validated reference deployment
advertised 25 tools; see the bundled skill reference for the version-scoped inventory and
its LLM or product dependencies.

## Configure

Create a dedicated, least-privilege, expiring API key and supply customer
values before starting Codex:

~~~bash
export DDSE_MCP_URL="https://<kibana-host>/api/agent_builder/mcp"
export DDSE_MCP_AUTHORIZATION="ApiKey <encoded-api-key>"
~~~

For a named Kibana space, use:

~~~text
https://<kibana-host>/s/<space-name>/api/agent_builder/mcp
~~~

Verify registration without printing the authorization value:

~~~toml
# ~/.codex/config.toml
[mcp_servers.dell_data_search_engine]
url = "https://<customer-kibana-host>/api/agent_builder/mcp"
env_http_headers = { Authorization = "DDSE_MCP_AUTHORIZATION" }
~~~

Then install and verify:

~~~bash
codex plugin add dell-data-search-engine@dell-harness-plugin
codex mcp get dell_data_search_engine --json
~~~

The `mcp get` check requires the preceding customer-owned `config.toml`
registration. It fails by design when only the skills plugin is installed,
because this publishable package does not embed a customer endpoint.

The marketplace package uses API-key authentication because that is the
validated self-managed DDSE contract. Browser SSO into Kibana is not MCP OAuth.
Claim MCP OAuth only when the exact supported DDSE release publishes OAuth
protected-resource metadata and documents the flow.

## Validate a deployment

From a machine that can reach the DDSE endpoint, with the API-key authorization
exported only in the process environment, capture and grade a sanitized tool
contract, then run the non-sensitive execution smoke. Pass `--ca-bundle` only
when the deployment uses a private certificate authority:

~~~bash
python scripts/capture_remote_mcp_contract.py \
  ddse /tmp/ddse-tools.json
python scripts/aidp_eval.py contract \
  dell-data-search-engine /tmp/ddse-tools.json --review-ready
python scripts/run_ddse_safe_smoke.py \
  /tmp/ddse-safe-smoke.json \
  --ca-bundle <customer-ca-bundle>
~~~

The smoke report is mode `0600` on POSIX and contains only response shape,
outcome, and latency—not raw search results, index names, or credentials.

How this package was validated, including the tool policy and dated evaluation
runs, is recorded in [the marketplace review record](../docs/validation/ddse-marketplace-review.md).

## Model dependencies

An Elastic chat connector is not required for deterministic index listing,
mapping retrieval, known-document lookup, exact ES|QL execution, or many
Observability and ML/AIOps retrieval tools. It is required by the validated
natural-language tools `platform_core_search`, `platform_core_generate_esql`,
and `platform_core_index_explorer`. Other tools can require installed content,
embeddings, ML jobs, solution data, licensing, or additional privileges even
when they do not require the Agent Builder chat model.

## Security notes

- Keep the endpoint and API key outside the package and inject them through an
  approved user or managed-device secret mechanism.
- Restrict the key to the intended Kibana spaces, application features, and
  index patterns, normally with `read` and `view_index_metadata` only.
- Grant inference privileges only for enabled tools that require them.
- Prefer expiring keys and rotate on scope, personnel, incident, or environment
  changes. Do not publish all-index or non-expiring credentials.
- Require trusted HTTPS certificates and allowlist the exact customer Kibana
  origin. Do not disable verification or package customer CA certificates.
- Review live tools for writes to cases, workflows, or stored state and disable
  or approval-gate them before a read-only deployment.

See the bundled `configure-dell-data-search-engine` skill for validation and
safe-use guidance. The bundled `investigate-dell-search-data` skill routes
user outcomes across the complete advertised tool catalog and defines the
evidence, dependency, and answer-format workflow.
