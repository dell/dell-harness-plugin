---
name: configure-dell-data-search-engine
description: >
  Configure, authenticate, validate, and safely use a customer-managed Dell
  Data Search Engine Kibana Agent Builder MCP endpoint from Codex. Use for DDSE
  or Elastic MCP setup, API-key authentication, Kibana spaces, tool catalogs,
  index search, ES|QL, Agent Builder model dependencies, observability,
  security investigation, permissions, or connection troubleshooting. Do not
  use for Dell Data Analytics Engine, Starburst, Trino, or Data Product requests.
---

# Configure Dell Data Search Engine

Use the native Kibana Agent Builder Streamable HTTP MCP endpoint. Do not point
the plugin at the Elasticsearch REST origin.

## Configure and authenticate

Obtain from the DDSE administrator:

- `DDSE_MCP_URL`: `https://<kibana-host>/api/agent_builder/mcp`, or
  `/s/<space-name>/api/agent_builder/mcp` for a named Kibana space.
- `DDSE_MCP_AUTHORIZATION`: `ApiKey <encoded-api-key>`, injected into the
  environment that launches Codex.

Never infer a customer endpoint. Never request an API key in chat or place it
in source, `.mcp.json`, command arguments, logs, or committed environment files.
The plugin intentionally does not embed a customer URL. Register the concrete
endpoint in the customer's Codex configuration:

~~~toml
[mcp_servers.dell_data_search_engine]
url = "https://<customer-kibana-host>/api/agent_builder/mcp"
env_http_headers = { Authorization = "DDSE_MCP_AUTHORIZATION" }
~~~

Set the complete authorization value in the environment that launches Codex;
quote it because the scheme and credential are separated by a space:

~~~bash
export DDSE_MCP_AUTHORIZATION="ApiKey <encoded-api-key>"
codex mcp get dell_data_search_engine --json
~~~

Keep customer-specific endpoints out of the reusable plugin package; each
customer registers its own MCP endpoint.

Use a dedicated, least-privilege, expiring API key for the validated
self-managed DDSE contract. Browser SSO into Kibana is not MCP OAuth. Claim
OAuth only when the exact supported release publishes protected-resource
metadata and product documentation for MCP clients.

## Interpret tools and model dependencies

Initialize MCP and treat live `tools/list` as authoritative. Release, license,
space, privileges, enabled features, installed content, and model configuration
can change the catalog.

The validated 25-tool reference deployment separated tools into:

- deterministic or product-native tools that do not require the Agent Builder
  chat model for core execution;
- `platform_core_search`, `platform_core_generate_esql`, and
  `platform_core_index_explorer`, which require a configured default chat
  model in the same Kibana space;
- conditional tools that require installed Elastic knowledge, embeddings, ML
  features, solution data, licensing, or further validation.

The Codex-first initial review selects only 10 deterministic read-only tools:
index listing, mapping, exact ES|QL, known-document retrieval, service and
dependency lookup, trace metrics, correlated logs, log-rate analysis, and log
categories. The remaining 15 tools may still appear in Elastic's `tools/list`,
but are conditional and are not selected by the initial-review cases. In
particular, `platform_core_search`, `platform_core_generate_esql`, and
`platform_core_index_explorer` remain disabled at the workflow level until the
same Kibana space has a positively validated Agent Builder chat connector.

Read `references/validated-tool-catalog.md` whenever a user asks for tool
availability, descriptions, model dependencies, prerequisites, or reference-deployment
evidence. Do not copy that baseline into a claim about another deployment.
Read `references/evaluation-plan.md` when preparing hero prompts, release
evidence, or OpenAI submission tests. Execute success, edge, and safety variants
against stable synthetic fixtures; do not treat documented cases as passing
until their transcripts and verifier results are retained.

Codex's own model does not replace a server-side Agent Builder connector when
the selected MCP tool internally calls Elastic inference. For a Codex-first
workflow without an Elastic chat model, use deterministic tools such as index
listing, mappings, known-document retrieval, or exact bounded ES|QL.

## Validate before data access

1. Confirm the URL targets Kibana Agent Builder MCP, not Elasticsearch REST.
2. Confirm TLS and hostname verification without a bypass.
3. Initialize MCP and record `tools/list` before assuming any capability.
4. Confirm tools, indices, and features match the intended Kibana space and
   least-privilege role.
5. Start with index listing or mappings; do not search customer data merely to
   test authentication.
6. Execute one bounded operation only after the user identifies an authorized
   data source.

## Enforce least privilege and safe usage

- Prefer a dedicated, expiring API key. Restrict Kibana application privileges
  to the intended space and Agent Builder or Actions features.
- Restrict indices to approved patterns with `read` and
  `view_index_metadata`; add inference monitoring only when required.
- Begin with mappings and bounded ES|QL. Minimize fields, rows, time ranges,
  buckets, and result payloads.
- Treat case, workflow, and other state-changing tools as writes even if they
  appear beside read tools. Require explicit approval or disable them.
- Treat observability and security results as sensitive. Do not expose API
  keys, authorization headers, unnecessary source fields, or secrets in logs.

## Troubleshoot

- `404` or `405`: verify the Kibana Agent Builder MCP path and space prefix.
- `401`: verify the `ApiKey ` scheme and rotate a potentially exposed key.
- `403`: correct the role descriptors; do not switch to an administrator key.
- Natural-language tool reports a missing connector or `connectorId`: configure
  and select the default Agent Builder chat model in the same Kibana space.
- Knowledge tool reports missing content or model artifacts: install the
  documented content and verify approved egress, licensing, and privileges.
- Missing tools or indices: verify release, license, feature flags, space,
  application privileges, and index privileges.
