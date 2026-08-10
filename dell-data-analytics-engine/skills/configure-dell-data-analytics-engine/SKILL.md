---
name: configure-dell-data-analytics-engine
description: >
  Configure, authenticate, validate, and safely use a customer-managed Dell
  Data Analytics Engine or Starburst Enterprise MCP endpoint from Codex. Use
  for DDAE OAuth, protected-resource discovery, Data Product search, read-only
  Trino SQL, tool-catalog inspection, model-dependency questions, or connection
  troubleshooting.
---

# Configure Dell Data Analytics Engine

Use the native DDAE/Starburst Streamable HTTP MCP server. Do not substitute a
local Trino bridge unless the user explicitly requests a legacy adapter.

## Configure and authenticate

Obtain from the DDAE administrator:

- `DDAE_MCP_URL`: complete HTTPS MCP endpoint, commonly
  `https://<ddae-host>/mcp`.
- `DDAE_OAUTH_RESOURCE`: resource identifier advertised by protected-resource
  metadata, commonly the DDAE origin without `/mcp`.

Never infer an endpoint from an example. Set both variables in the environment
that launches Codex, then run:

~~~bash
codex mcp login dell_data_analytics_engine --scopes openid
codex mcp get dell_data_analytics_engine --json
~~~

If dynamic client registration is disabled, require an administrator-approved
public OAuth client supporting authorization code, PKCE, and Codex's loopback
callback. Configure its ID with Codex `oauth_client_id`. Never embed a client
secret, access token, refresh token, endpoint, tenant, or certificate.

Do not confuse a Starburst CLI/JDBC browser handoff with MCP-native OAuth. Use
an alternative token handoff only when the supported DDAE product contract and
customer security policy explicitly approve it; keep the marketplace default
on standards-based MCP OAuth.

## Interpret the live catalog

Treat `tools/list` as authoritative. The validated Starburst Enterprise MCP
Server 1.0.0 advertised exactly:

- `searchDataProducts`: search Data Products visible to the OAuth user.
- `queryReadOnly`: execute one read-only Trino SQL statement.

Neither validated tool requires a DDAE-hosted generative model. Codex can
reason about the request and formulate SQL; DDAE performs OAuth,
authorization, read-only enforcement, execution, limits, and audit.

Read `references/validated-tool-catalog.md` for the validated schemas,
evidence, and version caveats.

## Validate before data access

1. Confirm the MCP URL returns an authentication challenge or MCP response,
   not a general DDAE UI page.
2. Confirm protected-resource metadata identifies the expected resource,
   authorization server, and scope.
3. Complete OAuth as the intended non-admin user.
4. Initialize MCP and inspect `tools/list`; do not infer tools that the server
   does not advertise.
5. Exercise `searchDataProducts` with a narrow or no-match query before reading
   data.
6. Run `queryReadOnly` only after the user identifies an authorized dataset and
   approves a bounded statement.

## Enforce safe usage

- Treat DDAE roles, catalog permissions, row and column controls, resource
  groups, and audit policy as authoritative.
- Reject DDL, DML, procedures, multi-statement SQL, and administrative SQL even
  if a downstream release unexpectedly accepts them.
- For `queryReadOnly`, use explicit columns, restrictive predicates, and a row
  limit. Avoid broad enumeration, `SELECT *`, and export-scale results.
- Do not log tokens, authorization headers, query results, or sensitive error
  details unnecessarily.

## Troubleshoot

- `401` before login: expected; complete OAuth.
- Protected-resource or audience error: verify `DDAE_OAUTH_RESOURCE` against
  live metadata.
- Dynamic registration rejected: use an administrator-approved public client.
- Redirect rejected: verify registered loopback URIs and PKCE policy.
- Login succeeds but resources are absent: verify DDAE roles; do not expand
  plugin permissions.
- Timeout or oversized result: reduce the scan and result set and confirm
  server-side limits.
