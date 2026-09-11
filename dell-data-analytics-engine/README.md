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
approved YAML query definitions, and restart the coordinator. They are not
enabled on the validated pilot endpoint. Installed release, license, feature
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

In the OpenAI marketplace submission, attach these skills to the reviewed
Universal MCP URL or approved URL template; do not put a customer-specific URL
in this package. Never embed a client secret or manually copy bearer tokens
into the plugin.

## OpenAI review authentication handoff

The review deployment uses the same MCP-native OAuth flow as production. Give
the reviewer a non-administrative test user in a production-like realm and
grant it access only to the versioned synthetic fixture. Do not send an access
token, refresh token, password, client secret, or customer URL in the plugin
archive or review notes.

Before submission, verify all of the following from the final hosted endpoint:

- protected-resource and issuer discovery complete successfully;
- DCR creates a native public client with PKCE S256 and no client secret;
- the reviewer sees the intended tool catalog and synthetic Data Products;
- a no-match search and `SELECT 1 AS validation_value LIMIT 1` succeed;
- a fresh sanitized contract scan has complete schemas and annotations; and
- at least five positive and three negative hero cases have retained ChatGPT
  and Codex transcript references, latency, and audit-event evidence.

The August pilot scan remains useful provenance, but it is not final review
evidence. Its missing `openWorldHint` annotations and optional `query` field are
server-owned contract gaps that must be corrected on the reviewed endpoint or
by an approved MCP gateway before submission.

An authenticated September 10, 2026 Codex revalidation completed OAuth and
successfully executed a unique no-match Data Product search plus
`SELECT 1 AS validation_value LIMIT 1`; both returned structured output. The
Codex-facing catalog exposed exactly `searchDataProducts` and `queryReadOnly`,
but marked neither tool's query input as required and did not surface any of
the three safety hints. Because that catalog view was not a raw,
pagination-aware `tools/list` capture, run the contract-capture command below
before attributing the discrepancy to the upstream server or submitting it.

With the private-network tunnel active and the Codex-managed OAuth session
enrolled, capture and grade a fresh sanitized contract from the
marketplace repository root:

~~~bash
python scripts/capture_remote_mcp_contract.py \
  ddae /tmp/ddae-tools.json
python scripts/aidp_eval.py contract \
  dell-data-analytics-engine /tmp/ddae-tools.json --review-ready
~~~

## Codex-first initial review evidence

The deterministic local review fixture contains only synthetic, non-customer
data and exposes the two selected read-only tools. The final September 10, 2026
Codex run passed all 12 selected cases: six positive and six negative. It covers
explicit, implicit, misspelled, ambiguous, authorization, write-refusal,
unbounded-export, and non-invocation behavior. ChatGPT transcripts are deferred
for the initial internal review and are not claimed as complete.

~~~bash
python scripts/aidp_eval.py fixture dell-data-analytics-engine
python scripts/run_ddae_codex_evals.py \
  evals/results/ddae-codex-initial-review-20260910/attempt-6
~~~

See [the review record](../evals/results/ddae-codex-initial-review-20260910/REVIEW.md)
and [machine-readable summary](../evals/results/ddae-codex-initial-review-20260910/attempt-6/summary.json).
The approved live smoke is recorded in
[live-safe-smoke.json](../evals/results/ddae-codex-initial-review-20260910/live-safe-smoke.json).
Local fixture and private-tunnel smoke evidence do not replace a raw contract
scan and reviewer-network test against the final hosted MCP endpoint.

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

## Marketplace and security notes

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
