# DDAE OAuth user guide

Use this guide to connect Codex to a Dell Data Analytics Engine (DDAE) MCP
endpoint, sign in with your normal DDAE identity, and verify your authorized
tools. This guide is for plugin users. DDAE and identity-provider administrators
should use [DDAE OAuth administrator guide](DDAE_OAUTH_ADMIN_GUIDE.md).

The standard setup assumes the Codex runtime can reach the DDAE and OAuth
endpoints, uses trusted TLS certificates, and can complete a browser login with
a loopback callback on the workstation running Codex.

## Contents

- [Before you begin](#before-you-begin)
- [1. Register the MCP endpoint](#1-register-the-mcp-endpoint)
- [2. Sign in](#2-sign-in)
- [3. Verify the connection](#3-verify-the-connection)
- [4. Run a safe smoke test](#4-run-a-safe-smoke-test)
- [5. Use DDAE safely](#5-use-ddae-safely)
- [Troubleshooting](#troubleshooting)
- [Information to give an administrator](#information-to-give-an-administrator)

## Before you begin

Obtain these items from the DDAE administrator:

- The complete MCP endpoint, normally `https://<ddae-host>/mcp`.
- Confirmation that your user identity has the intended DDAE roles and Data
  Product permissions.
- A public OAuth client ID only when the administrator has disabled dynamic
  client registration and explicitly provides a pre-registered fallback.

Do not request or accept a client secret. Codex uses a native public OAuth
client with authorization code and PKCE.

## 1. Register the MCP endpoint

Register only the endpoint when DDAE protected-resource discovery is correctly
configured:

~~~bash
export DDAE_MCP_URL="https://<ddae-host>/mcp"

codex mcp add dell_data_analytics_engine \
  --url "$DDAE_MCP_URL"

codex mcp get dell_data_analytics_engine --json
~~~

Do not add an OAuth resource override unless the administrator explicitly says
discovery is unavailable or incorrect. Supplying a resource both manually and
through discovery can produce `invalid_request: duplicated parameter`.

## 2. Sign in

Start the minimal interactive OAuth flow:

~~~bash
codex mcp login dell_data_analytics_engine --scopes openid
~~~

Complete the browser login with your normal, non-admin DDAE identity. Codex
uses PKCE and a one-time loopback callback. When dynamic client registration is
enabled, the authorization server generates the `client_id`; you do not enter
one and no client secret is created.

Keep `--scopes openid` explicit. The validated Codex client also requested
`offline_access` for refresh-token support. Omitting `--scopes` can request
unrelated scopes advertised by the issuer.

Never paste the callback URL, authorization code, access token, refresh token,
password, or authorization header into chat, logs, documentation, or tickets.

## 3. Verify the connection

`codex mcp get` confirms local registration but does not by itself prove an
authenticated MCP initialization. In a new Codex thread, ask:

> Validate the configured DDAE MCP connection and list the tools visible to my
> identity. Do not search Data Products, query data, or execute SQL.

The live `tools/list` response is authoritative. The validated Starburst
Enterprise MCP Server 1.0.0 identity exposed `searchDataProducts` and
`queryReadOnly`; other releases, configurations, and identities can differ.

## 4. Run a safe smoke test

After tool discovery succeeds, ask Codex to execute exactly one synthetic,
bounded statement through `queryReadOnly`:

~~~sql
SELECT 1 AS validation_value LIMIT 1
~~~

The expected result is one integer column, one row containing `1`, and a DDAE
query ID. This verifies authenticated tool execution without reading a customer
catalog, table, or Data Product.

## 5. Use DDAE safely

- Search for a narrow business term instead of enumerating every Data Product.
- Use only Data Products and fields returned to your authenticated identity.
- Approve read-only queries that select explicit columns, use restrictive
  predicates, and include a stable row limit.
- Do not request DDL, DML, procedures, administrative SQL, multiple statements,
  `SELECT *`, or export-scale results.
- Treat DDAE authorization errors and empty results as authoritative.

## Troubleshooting

| Symptom | User action |
|---|---|
| `401` before login | Run the login command and complete browser authentication. |
| `invalid_request: duplicated parameter` | Remove a redundant OAuth resource override and start a new login. |
| `invalid_grant: Code not valid` | Start a new login. The old authorization code is single-use or expired; do not reuse or expose it. |
| Dynamic registration, scope, audience, or redirect error | Stop retrying and give the redacted error to the DDAE/OAuth administrator. |
| Login succeeds but tools or data are absent | Ask the administrator to verify your intended DDAE roles. Do not switch to a more privileged identity. |
| Refresh fails after the initial token expires | Run login again and ask the administrator to verify approved refresh and client-lifecycle policy. |
| Query is denied, times out, or is too large | Preserve the denial or reduce the query scope and result size; do not bypass DDAE limits. |

## Information to give an administrator

Provide only:

- Timestamp and timezone of the failure.
- MCP endpoint and discovered issuer hostname.
- Redacted error code and description.
- Generated public `client_id`, if one was returned and the administrator needs
  it to locate registration events.
- Whether failure occurred during discovery, registration, browser login, code
  exchange, MCP initialization, tool listing, or refresh.

Never provide a password, client secret, callback URL, authorization code,
access token, refresh token, or complete authorization header.
