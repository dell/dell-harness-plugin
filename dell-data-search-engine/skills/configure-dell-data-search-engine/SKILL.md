---
name: configure-dell-data-search-engine
description: >
  Configure, authenticate, validate, and safely use a customer-managed Dell
  Data Search Engine Kibana Agent Builder MCP endpoint from Codex. Use when a
  user asks about DDSE or Elastic MCP setup, API-key authentication, Kibana
  spaces, index search, ES|QL, observability, security investigation, or
  connection troubleshooting.
---

# Configure Dell Data Search Engine

Use the native Kibana Agent Builder Streamable HTTP MCP endpoint. Do not point
the plugin at the Elasticsearch REST origin: that service is not the MCP
endpoint.

## Required configuration

Obtain these values from the DDSE administrator:

- `DDSE_MCP_URL`: `https://<kibana-host>/api/agent_builder/mcp`, or the
  `/s/<space-name>/api/agent_builder/mcp` form for a non-default Kibana space.
- `DDSE_MCP_AUTHORIZATION`: `ApiKey <api-key>`, injected into the environment
  that launches Codex.

Never infer a customer endpoint from a Dell lab example. Never ask the user to
paste an API key into chat, source code, `.mcp.json`, a command-line argument,
or a committed environment file.

For self-managed DDSE, use API-key authentication. OAuth for Elastic MCP is a
separate Elastic Serverless capability and must not be claimed for a
self-managed deployment unless its product documentation explicitly supports
it.

## Validate before data access

1. Confirm the URL is the Kibana Agent Builder MCP path, not Elasticsearch REST.
2. Confirm TLS and the expected Kibana hostname without disabling certificate
   verification.
3. Initialize MCP and inspect `tools/list`; do not search customer data merely
   to test authentication.
4. Confirm the returned tools and indices match the intended Kibana space and
   role.
5. Execute a small bounded query only after the user identifies an authorized
   data source.

## Least-privilege design

- Create a dedicated, expiring API key rather than reusing an administrator key.
- Restrict Kibana application privileges to the intended space and Agent
  Builder/actions features.
- Restrict index privileges to the required index patterns, normally `read`
  and `view_index_metadata` only.
- Grant inference monitoring only when the enabled tools require it.
- Treat the live server tool inventory as authoritative; capability sets vary
  by release, license, role, and space.

## Safe usage

- Begin with mappings and bounded ES|QL; avoid broad index scans.
- Do not expose API keys or authorization headers in output or logs.
- Ask before operations that could change cases, workflows, or stored data.
- Treat observability and security results as potentially sensitive.

## Troubleshooting

- `404` or `405`: verify the request targets Kibana's
  `/api/agent_builder/mcp`, not the Elasticsearch origin.
- `401`: verify the value begins with `ApiKey ` and rotate the key if it may
  have been exposed.
- `403`: reduce or correct the key's role descriptors; do not switch to an
  administrator key as a diagnostic shortcut.
- Missing tools or indices: verify the Elastic version, license, feature flags,
  Kibana space, and application/index privileges.
