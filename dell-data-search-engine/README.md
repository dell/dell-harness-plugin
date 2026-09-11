# Dell Data Search Engine for Codex

This plugin supplies reusable Codex skills for the native Kibana Agent Builder
Streamable HTTP MCP endpoint in a customer-managed Dell Data Search Engine
(DDSE) deployment. The customer registers the MCP endpoint separately. The
package contains no endpoint, API key, Kibana space, certificate, or customer
data.

## Customer capabilities

Depending on the Elastic release, license, Kibana space, installed content,
model configuration, and assigned privileges, customers can:

- Discover authorized indices and mappings and retrieve known documents.
- Execute exact, bounded ES|QL and investigate governed search data.
- Explore logs, traces, hosts, services, alerts, cases, and change points.
- Inspect integrations, workflows, and installed Elastic knowledge content.
- Use natural-language search and ES|QL generation when an Agent Builder chat
  model is configured in the same Kibana space.

The live `tools/list` response is authoritative. The validated pilot advertised
25 tools; see the bundled skill reference for the version-scoped inventory and
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

In the OpenAI marketplace submission, attach these skills to the reviewed
Universal MCP URL or approved URL template; do not put a customer-specific URL
in this package.

The marketplace package uses API-key authentication because that is the
validated self-managed DDSE contract. Browser SSO into Kibana is not MCP OAuth.
Claim MCP OAuth only when the exact supported DDSE release publishes OAuth
protected-resource metadata and documents the flow.

## OpenAI review authentication handoff

Create a dedicated, expiring review API key rather than reusing a pilot or
administrator credential. Bind it to the review Kibana space, synthetic index
patterns, and the minimum application privileges needed by the submitted tool
surface. Deliver the complete `ApiKey ...` authorization value through the
review portal's protected credential mechanism; never place it in the plugin
archive, prompts, transcripts, screenshots, or source control.

Before submission, verify all of the following from the final hosted endpoint:

- TLS and the exact Kibana Agent Builder MCP URL work from the review runtime;
- the review key sees only the intended space, indices, and read-only tools;
- every submitted tool has typed input and output schemas plus
  `readOnlyHint`, `destructiveHint`, and `openWorldHint` annotations;
- inference-dependent and installed-content tools are either provisioned and
  fixture-tested or removed from the submitted v1 surface; and
- at least five positive and three negative hero cases have retained Codex
  transcript references, latency, and audit-event evidence. ChatGPT transcripts
  are deferred for the initial review and remain a later review artifact.

The authenticated September 10, 2026 scan confirms that the native 25-tool
server still omits output schemas and safety annotations. Those are server-owned
contract gaps. Correct them in the reviewed DDSE release or through an approved
MCP gateway; skill instructions cannot substitute for tool metadata. The same
scan confirmed that the previously enrolled API key still authenticates without
invoking a product tool or reading customer data.

A separate sanitized live smoke then exercised only synthetic nonexistent names
and an index-free constant ES|QL expression. Index discovery, missing-index
mapping, and constant ES|QL execution succeeded in 120 ms, 119 ms, and 384 ms;
an omitted required mapping argument correctly returned a tool error in 130 ms.
Every response contained one text content item and no `structuredContent`, which
independently confirms the upstream structured-output gap. Raw tool content was
neither printed nor retained.

With the private-network tunnel active and the API-key authorization exported
only in the process environment, capture and grade a fresh sanitized contract
from the marketplace repository root:

~~~bash
python scripts/capture_remote_mcp_contract.py \
  ddse /tmp/ddse-tools.json
python scripts/aidp_eval.py contract \
  dell-data-search-engine /tmp/ddse-tools.json --review-ready
~~~

After contract capture, run the non-sensitive execution smoke separately. The
example uses the pilot's temporary network path; replace the proxy, resolution,
and CA values for the review environment:

~~~bash
python scripts/run_ddse_safe_smoke.py \
  /tmp/ddse-safe-smoke.json \
  --proxy socks5://127.0.0.1:1080 \
  --resolve <kibana-host>=<service-ip> \
  --ca-bundle <customer-ca-bundle> \
  --legacy-ca-compat
~~~

The smoke report is mode `0600` on POSIX and contains only response shape,
outcome, and latency—not raw search results, index names, or credentials.

## Model dependencies

An Elastic chat connector is not required for deterministic index listing,
mapping retrieval, known-document lookup, exact ES|QL execution, or many
Observability and ML/AIOps retrieval tools. It is required by the validated
natural-language tools `platform_core_search`, `platform_core_generate_esql`,
and `platform_core_index_explorer`. Other tools can require installed content,
embeddings, ML jobs, solution data, licensing, or additional privileges even
when they do not require the Agent Builder chat model.

### Initial-review tool policy

The Codex-first initial review selects 10 deterministic tools: index listing,
mapping, exact ES|QL, known-document retrieval, service and dependency lookup,
trace metrics, correlated logs, log-rate analysis, and log categories. Six
positive and three negative synthetic cases cover that surface. The other 15
advertised tools are conditional and are not used by the selected cases.

The three Agent Builder inference tools remain documented but out of the
default path until a default chat connector is positively validated in the
same Kibana space. Installed-content, embedding, ML, case, workflow, alert,
host, change-point, index-info, and security tools likewise remain conditional
until their individual data, license, privilege, and fixture prerequisites are
validated. Because filtering is workflow-level, these tools may still appear
in the upstream Elastic `tools/list`; the plugin does not claim to remove them
from the server.

For the initial review, genuine Codex JSONL transcripts and sanitized audit
events derived from those transcripts are required. ChatGPT transcript capture
is explicitly deferred and is not counted as an initial-review failure.
Validate the fixture contract and run the Codex matrix from the repository root:

~~~bash
python scripts/aidp_eval.py fixture dell-data-search-engine
python scripts/run_ddse_codex_evals.py \
  evals/results/ddse-codex-initial-review
~~~

The September 10, 2026 final Codex run passed all nine selected cases (six
positive and three negative). Its sanitized evidence index and per-case JSONL
transcripts are in
`evals/results/ddse-codex-initial-review-20260910/REVIEW.md` and the final
attempt directory linked from that index.
This closes the local fixture-evidence gap; it does not close the upstream
output-schema and safety-annotation gaps described above.

## Marketplace and security notes

- Keep the endpoint and API key outside the package and inject them through an
  approved user or managed-device secret mechanism.
- Restrict the key to the intended Kibana spaces, application features, and
  index patterns, normally with `read` and `view_index_metadata` only.
- Grant inference privileges only for enabled tools that require them.
- Prefer expiring keys and rotate on scope, personnel, incident, or environment
  changes. Do not publish all-index, non-expiring pilot credentials.
- Require trusted HTTPS certificates and allowlist the exact customer Kibana
  origin. Do not disable verification or package customer CA certificates.
- Review live tools for writes to cases, workflows, or stored state and disable
  or approval-gate them before a read-only deployment.

See the bundled `configure-dell-data-search-engine` skill for validation and
safe-use guidance. The bundled `investigate-dell-search-data` skill routes
user outcomes across the complete advertised tool catalog and defines the
evidence, dependency, and answer-format workflow.
