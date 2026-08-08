# Dell Data Analytics Engine for Codex

This plugin connects Codex to the native MCP server in a customer-managed
Dell Data Analytics Engine (DDAE) deployment. It does not ship an endpoint,
credential, tenant identifier, or customer data.

## Customer capabilities

- Discover the catalogs, schemas, tables, and columns authorized for the user.
- Ask questions about governed lakehouse data and generate SQL from natural
  language.
- Execute read-only SQL through the Starburst Enterprise authorization layer.
- Analyze returned data while preserving the platform's user-level access
  controls, query limits, and audit path.

The exact tool inventory is supplied by the installed DDAE release and may
vary by administrator policy.

## Configure

Supply the values for the customer's deployment before starting Codex:

```bash
export DDAE_MCP_URL="https://<ddae-host>/mcp"
export DDAE_OAUTH_RESOURCE="https://<ddae-host>"
```

Install the plugin and complete interactive OAuth login:

```bash
codex plugin add dell-data-analytics-engine@dell-harness-plugin
codex mcp login dell_data_analytics_engine --scopes openid
codex mcp get dell_data_analytics_engine --json
```

The OAuth resource is normally the DDAE origin, not the `/mcp` path. Confirm
both values with the customer's DDAE administrator because reverse proxies
can publish a different resource identifier.

## Marketplace and security notes

- The endpoint is mandatory configuration and has no Dell lab default.
- OAuth tokens are managed by Codex; they are not stored in this plugin.
- DDAE roles remain the source of truth for catalog and object access.
- Administrators should allowlist the exact production MCP origin in managed
  Codex policy and outbound network controls.
- Enable the DDAE AI MCP Server feature only after licensing, identity,
  authorization, TLS, query-limit, and audit requirements have been reviewed.

See the bundled `configure-dell-data-analytics-engine` skill for validation and
troubleshooting. For private deployments, see
[`MCP_PRIVATE_NETWORK_TESTING.md`](../MCP_PRIVATE_NETWORK_TESTING.md).
