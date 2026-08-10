# Dell Data Analytics Engine for Codex

This plugin connects Codex to the native Streamable HTTP MCP server in a
customer-managed Dell Data Analytics Engine (DDAE) deployment. It contains no
endpoint, credential, tenant identifier, certificate, or customer data.

## Customer capabilities

- Search governed Data Products authorized for the OAuth user.
- Form bounded Trino SQL from natural-language requirements.
- Execute read-only SQL through Starburst Enterprise authorization.
- Analyze results while preserving DDAE roles, row and column controls,
  resource management, query limits, and audit.

The live `tools/list` response is authoritative. Starburst Enterprise MCP
Server 1.0.0 was validated with `searchDataProducts` and `queryReadOnly`; that
version-scoped observation is not a promise for every DDAE deployment.

The current Starburst Enterprise documentation describes a broader product
surface: the three built-in tools `queryReadOnly`, `searchDataProducts`, and
`getDataProductDetails`; the optional `listParametrizedQueryTools` and
`parametrizedQuery` tools when parameterized queries are enabled; and an
optional Starburst AI data-product agent tool when a model alias is configured.
Installed release, license, feature configuration, administrator definitions,
and authorization determine what a customer endpoint actually advertises.

## Configure

Supply customer-selected values before starting Codex:

~~~bash
export DDAE_MCP_URL="https://<ddae-host>/mcp"
export DDAE_OAUTH_RESOURCE="https://<ddae-host>"
~~~

Install the plugin and complete native MCP OAuth:

~~~bash
codex plugin add dell-data-analytics-engine@dell-harness-plugin
codex mcp login dell_data_analytics_engine --scopes openid
codex mcp get dell_data_analytics_engine --json
~~~

Confirm the resource identifier from the endpoint's protected-resource
metadata. The authorization server must permit standards-based dynamic client
registration or provide an administrator-approved public client ID compatible
with authorization code, PKCE, and Codex's loopback callback. Never embed a
client secret or manually copy bearer tokens into the plugin.

## Marketplace and security notes

- OAuth tokens are managed by Codex and remain outside the package.
- DDAE authorization and query controls are the enforcement boundary; skill
  text is guidance, not a security control.
- The two validated tools do not require a DDAE-hosted generative model. Codex
  may formulate SQL; DDAE authenticates, authorizes, enforces read-only access,
  executes, limits, and audits it.
- Require trusted HTTPS certificates and allowlist the exact customer MCP and
  authorization-server origins. Do not package customer CA certificates.
- Enable the DDAE AI MCP Server only after licensing, identity, authorization,
  query-limit, data-classification, and audit requirements are approved.

See the bundled `configure-dell-data-analytics-engine` skill for validation and
safe-use guidance.
