---
name: configure-dell-data-analytics-engine
description: >
  Configure, authenticate, validate, and safely use a customer-managed Dell
  Data Analytics Engine or Starburst Enterprise MCP endpoint from Codex. Use
  for DDAE OAuth, protected-resource discovery, Data Product search, read-only
  Trino SQL, tool-catalog inspection, parameterized-query configuration, or
  connection troubleshooting. Do not use for Dell Data Search Engine, Elastic,
  Kibana, or ES|QL requests.
---

# Configure Dell Data Analytics Engine

Use the native DDAE/Starburst Streamable HTTP MCP server. Do not substitute a
local Trino bridge unless the user explicitly requests a legacy adapter.

## Configure and authenticate

Obtain from the DDAE administrator:

- `DDAE_MCP_URL`: complete HTTPS MCP endpoint, commonly
  `https://<ddae-host>/mcp`.
- The protected-resource metadata URL and expected authorization-server issuer.
- The resource identifier advertised by that metadata, commonly the DDAE origin
  without `/mcp`.

Never infer an endpoint from an example. The plugin intentionally does not
embed a customer URL. Register the concrete endpoint in each customer's Codex
configuration, then complete OAuth:

~~~bash
codex mcp add dell_data_analytics_engine \
  --url "$DDAE_MCP_URL"
codex mcp login dell_data_analytics_engine --scopes openid
codex mcp get dell_data_analytics_engine --json
~~~

Prefer protected-resource discovery over a manual OAuth resource override. If
discovery already supplies the resource, an explicit override can cause some
clients to send `resource` twice and receive `invalid_request: duplicated
parameter`. Use a manual override only when discovery is absent or incorrect,
and verify that the final authorization request contains exactly one resource.

Keep `--scopes openid` explicit. In the validated Codex flow, the client also
requested `offline_access` for refresh-token support. Omitting `--scopes` can
request every scope advertised by the issuer, including scopes unrelated to
interactive MCP access.

Keep customer-specific endpoints out of the reusable plugin package; each
customer registers its own MCP endpoint.

Use dynamic client registration by default when the customer permits it. The
authorization server must accept a native public client with
`grant_types=[authorization_code]`, `token_endpoint_auth_method=none`, an exact
loopback redirect URI, and PKCE S256. Registration returns a generated
`client_id` and no client secret. A realm-level default client scope or
equivalent policy must add the DDAE-required audience to every DCR-created
client's access token.

If dynamic client registration is disabled, require an administrator-approved
public OAuth client with the same authorization-code, PKCE, audience, and
loopback behavior. Configure its ID with Codex `oauth_client_id`. Never embed a
client secret, access token, refresh token, endpoint, tenant, or certificate.

Do not confuse a Starburst CLI/JDBC browser handoff with MCP-native OAuth. Use
an alternative token handoff only when the supported DDAE product contract and
customer security policy explicitly approve it; keep the marketplace default
on standards-based MCP OAuth.

## Interpret the live catalog

Treat `tools/list` as authoritative. The validated Starburst Enterprise MCP
Server 1.0.0 advertised exactly:

- `searchDataProducts`: search Data Products visible to the OAuth user.
- `queryReadOnly`: execute one read-only Trino SQL statement.

That response contained one page and no `nextCursor`, so two is complete for
the tested endpoint and identity. Do not generalize it to the DDAE product.
Current Starburst documentation also describes built-in
`getDataProductDetails` and optional `listParametrizedQueryTools` and
`parametrizedQuery`. The parameterized tools require administrator action:
set `mcp.parametrized-tools.enabled=true`, configure
`mcp.parametrized-tools.definition-paths`, add approved YAML definitions, and
restart because definitions load at server startup. Their absence from the
validated live catalog means they are not enabled or not exposed on that
endpoint. Compare live output with documentation for the installed release and
investigate differences through release, license, configuration, definitions,
and authorization rather than inventing or assuming tools.

Codex can reason about the request and formulate SQL; DDAE performs OAuth,
authorization, read-only enforcement, execution, limits, and audit. The two
validated tools require no separate DDAE inference configuration.

Read `references/validated-tool-catalog.md` for the validated schemas,
evidence, and version caveats.
Read `references/evaluation-plan.md` when preparing hero prompts, release
evidence, or marketplace review tests. Execute success, edge, and safety variants
against stable synthetic fixtures; do not treat documented cases as passing
until their transcripts and verifier results are retained.
Read `references/DDAE_OAUTH_USER_GUIDE.md` when helping a plugin user register
the endpoint, sign in, verify tools, run a safe smoke test, or prepare a redacted
support request.
Read `references/DDAE_OAUTH_ADMIN_GUIDE.md` when preparing DDAE or the identity
provider, configuring DCR or a fallback public client, mapping audience and
identity claims, or troubleshooting server-side OAuth policy.

## Validate before data access

1. Confirm the MCP URL returns an authentication challenge or MCP response,
   not a general DDAE UI page.
2. Confirm protected-resource metadata identifies the expected resource,
   authorization server, and scope.
3. Confirm issuer metadata advertises the expected authorization, token, and
   registration endpoints and PKCE S256 support.
4. Complete OAuth as the intended non-admin user. Confirm a DCR-created client
   is public, uses authorization code, has no secret, and receives the required
   DDAE audience.
5. Initialize MCP and inspect `tools/list`; do not infer tools that the server
   does not advertise.
6. Exercise `searchDataProducts` with a narrow or no-match query before reading
   data.
7. Run `queryReadOnly` only after the user identifies an authorized dataset and
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
- `invalid_request: duplicated parameter`: inspect the authorization URL for
  duplicate `resource` values and remove the redundant manual override.
- `403 insufficient_scope` from DCR: update the authorization server's allowed
  client-scopes policy for the minimal interactive scopes; do not broaden the
  user's DDAE permissions.
- Protected-resource or audience error: verify live metadata and ensure the
  DDAE-required audience mapper applies to every newly registered client.
- Dynamic registration rejected: correct the DCR policy or use an
  administrator-approved public client.
- Redirect rejected: verify the exact registered loopback URI and PKCE S256
  policy. Loopback uses `127.0.0.1`, not the DDAE hostname.
- `invalid_grant: Code not valid`: restart login; authorization codes are
  single-use and short-lived. Do not paste them into chat, logs, or tickets.
- Login succeeds but resources are absent: verify DDAE roles; do not expand
  plugin permissions.
- Timeout or oversized result: reduce the scan and result set and confirm
  server-side limits.
