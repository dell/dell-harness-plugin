---
name: configure-dell-data-analytics-engine
description: >
  Configure, authenticate, validate, and safely use a customer-managed Dell
  Data Analytics Engine MCP endpoint from Codex. Use when a user asks about
  DDAE or Starburst MCP setup, OAuth login, catalog discovery, read-only SQL,
  endpoint validation, or connection troubleshooting.
---

# Configure Dell Data Analytics Engine

Use the native DDAE/Starburst Streamable HTTP MCP server. Do not substitute a
locally hosted Trino bridge unless the user explicitly asks for that legacy
architecture.

## Required configuration

Obtain these non-secret values from the DDAE administrator:

- `DDAE_MCP_URL`: the complete HTTPS MCP endpoint, commonly
  `https://<ddae-host>/mcp`.
- `DDAE_OAUTH_RESOURCE`: the protected-resource identifier advertised by the
  server, commonly the DDAE origin without `/mcp`.

Never infer a customer endpoint from a Dell lab example. Require HTTPS outside
an isolated development environment.

Set both variables in the environment used to launch Codex, then run:

```bash
codex mcp login dell_data_analytics_engine --scopes openid
codex mcp get dell_data_analytics_engine --json
```

If the identity provider does not permit dynamic client registration, stop and
ask the DDAE administrator for an approved OAuth client. Configure it using the
Codex `oauth_client_id` setting; never embed a client secret in this plugin.

## Validate before data access

1. Check the MCP URL returns an authentication challenge or MCP response, not
   a general DDAE UI page.
2. Confirm protected-resource metadata identifies the expected authorization
   server and resource.
3. Complete interactive OAuth login as the current user.
4. List MCP tools and catalogs; do not execute a query merely to test login.
5. Run a small read-only query only after the user identifies an authorized
   dataset.

## Safe usage

- Treat DDAE roles and row/column controls as authoritative.
- Keep generated SQL read-only. Reject DDL, DML, procedures, and multi-statement
  SQL even if a downstream deployment unexpectedly accepts them.
- Start with metadata and bounded queries; avoid broad scans and `SELECT *`.
- Do not present query generation as proof that the user can execute it.
- Do not log tokens, authorization headers, or sensitive result rows.

## Troubleshooting

- `401` before login: expected; complete `codex mcp login`.
- OAuth audience/resource error: verify `DDAE_OAUTH_RESOURCE` against protected
  resource metadata.
- Dynamic registration rejected: use an administrator-approved public client.
- Login succeeds but catalogs are absent: verify DDAE user roles rather than
  expanding plugin permissions.
- Timeout or oversized result: reduce the scan and result set; DDAE can enforce
  server-side MCP query and response limits.
