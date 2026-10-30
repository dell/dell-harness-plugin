# Dell Data Analytics Engine for Codex

This plugin supplies reusable Codex skills for the native Streamable HTTP MCP
server in a customer-managed Dell Data Analytics Engine (DDAE) deployment.
The customer registers the MCP endpoint separately. The package contains no
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
`getDataProductDetails`, plus the optional `listParametrizedQueryTools` and
`parametrizedQuery` tools. The optional tools require a DDAE/Starburst
administrator to enable them in the coordinator configuration, install
approved YAML query definitions, and restart the coordinator. They were not
enabled on the validated reference deployment. Installed release, license, feature
configuration, administrator definitions, and authorization determine what a
customer endpoint actually advertises.

## Configure

Supply the customer-selected MCP endpoint before starting Codex:

~~~bash
export DDAE_MCP_URL="https://<ddae-host>/mcp"
~~~

Install the plugin and complete native MCP OAuth:

~~~bash
codex plugin add dell-data-analytics-engine@dell-harness-plugin
codex mcp add dell_data_analytics_engine \
  --url "$DDAE_MCP_URL"
codex mcp login dell_data_analytics_engine --scopes openid
codex mcp get dell_data_analytics_engine --json
~~~

The tested configuration relies on the endpoint's protected-resource metadata
to supply the OAuth resource and authorization server. Do not also configure an
OAuth resource override when discovery is correct: some client versions can
otherwise place `resource` in the authorization request twice, which the
authorization server rejects as a duplicated parameter.

The authorization server should permit standards-based dynamic client
registration (DCR) for a native public client using authorization code and
PKCE S256. Codex receives the generated `client_id`; an administrator does not
normally preconfigure one. A public client has no client secret. If DCR is not
permitted, use an administrator-approved public client ID with equivalent
grant, PKCE, and loopback-redirect behavior.

Always pass the minimal explicit login scope. In the validated flow,
`--scopes openid` produced an authorization request for `openid offline_access`
because Codex requested refresh-token support. Omitting `--scopes` can cause a
client to request every scope advertised by the issuer.

Never embed a client secret or manually copy bearer tokens into the plugin.

## Validate a deployment

From a machine that can reach the DDAE endpoint, with the Codex-managed OAuth
session enrolled, capture and grade a sanitized tool contract from the
marketplace repository root:

~~~bash
python scripts/capture_remote_mcp_contract.py \
  ddae /tmp/ddae-tools.json
python scripts/aidp_eval.py contract \
  dell-data-analytics-engine /tmp/ddae-tools.json --review-ready
~~~

How this package was validated, including the dated evaluation runs, is
recorded in [the marketplace review record](../docs/validation/ddae-marketplace-review.md).

## OAuth guides

- [DDAE OAuth user guide](skills/configure-dell-data-analytics-engine/references/DDAE_OAUTH_USER_GUIDE.md):
  endpoint registration, user login, connection verification, safe smoke test,
  and user-level troubleshooting.
- [DDAE OAuth administrator guide](skills/configure-dell-data-analytics-engine/references/DDAE_OAUTH_ADMIN_GUIDE.md):
  protected-resource and issuer discovery, Keycloak DCR policy, audience and
  identity claims, fallback clients, DDAE authorization, and operations.

### Administrator prerequisites

The DDAE and identity administrators must configure protected-resource and
authorization-server discovery, accept the native public DCR profile, permit
the required scopes, and ensure access tokens include the audience expected by
DDAE. The audience mapper must apply automatically to newly registered clients;
adding it only to one test client does not make future DCR clients usable.

## Security notes

- OAuth tokens are managed by Codex and remain outside the package.
- DDAE authorization and query controls are the enforcement boundary; skill
  text is guidance, not a security control.
- Codex may formulate SQL; DDAE authenticates, authorizes, enforces read-only
  access, executes, limits, and audits it. No separate DDAE inference setup is
  required for the two validated tools.
- Require trusted HTTPS certificates and allowlist the exact customer MCP and
  authorization-server origins. Do not package customer CA certificates.
- Enable the native DDAE MCP server only after licensing, identity, authorization,
  query-limit, data-classification, and audit requirements are approved.

See the bundled `configure-dell-data-analytics-engine` skill for validation and
safe-use guidance. The bundled `analyze-governed-data-products` skill provides
the user-facing discovery-to-analysis workflow and grounded answer format.
