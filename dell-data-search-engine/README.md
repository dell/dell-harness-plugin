# Dell Data Search Engine for Codex

This plugin connects Codex to the Kibana Agent Builder MCP endpoint in a
customer-managed Dell Data Search Engine (DDSE) deployment. It does not ship an
endpoint, API key, tenant/space name, or customer data.

## Customer capabilities

Depending on the installed Elastic release, licensed features, Kibana space,
and assigned privileges, customers can:

- Discover indices, mappings, fields, and individual authorized documents.
- Generate and execute ES|QL for search, aggregation, and investigation.
- Explore logs, traces, hosts, services, and change points.
- Investigate Elastic Security alerts and cases.
- Inspect integrations, workflows, and relevant Elastic product guidance.

The live MCP `tools/list` response is authoritative; the plugin does not assume
every tool is enabled for every customer.

## Configure

Create a dedicated, least-privilege, expiring API key. Supply the customer
values before starting Codex:

```bash
export DDSE_MCP_URL="https://<kibana-host>/api/agent_builder/mcp"
read -rsp "DDSE API key: " DDSE_API_KEY; echo
export DDSE_MCP_AUTHORIZATION="ApiKey ${DDSE_API_KEY}"
unset DDSE_API_KEY
```

For a non-default Kibana space, use:

```text
https://<kibana-host>/s/<space-name>/api/agent_builder/mcp
```

Then install and verify registration without printing the authorization value:

```bash
codex plugin add dell-data-search-engine@dell-harness-plugin
codex mcp get dell_data_search_engine --json
```

## Marketplace and security notes

- The endpoint and authorization value are mandatory configuration; neither has
  a Dell lab default.
- The API key remains outside the plugin and must be injected by an approved
  secret-management or managed-device mechanism.
- Use a dedicated service identity or user API key with only the Kibana space,
  index, inference, and feature privileges required by the use case.
- Administrators should allowlist the exact Kibana MCP origin in managed Codex
  policy and outbound network controls.
- Rotate the key on personnel, scope, incident, or environment changes.

See the bundled `configure-dell-data-search-engine` skill for validation and
troubleshooting. For private deployments, see
[`MCP_PRIVATE_NETWORK_TESTING.md`](../MCP_PRIVATE_NETWORK_TESTING.md).
