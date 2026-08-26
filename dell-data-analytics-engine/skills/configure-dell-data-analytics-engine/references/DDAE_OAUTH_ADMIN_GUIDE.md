# DDAE OAuth administrator guide

This guide describes the DDAE and identity-provider configuration required for
Codex to use the native Dell Data Analytics Engine (DDAE) Streamable HTTP MCP
server through user-delegated OAuth. It is based on the validated public-client
dynamic registration and PKCE flow. Replace every placeholder with values for
the customer deployment.

This guide is for DDAE, Keycloak, and security administrators. Give plugin users
the separate [DDAE OAuth user guide](DDAE_OAUTH_USER_GUIDE.md); it contains the
endpoint registration, login, verification, and user-level troubleshooting
workflow.

The plugin contains no endpoint, client identifier, secret, token, certificate,
tenant, or customer data. DDAE remains the authorization, read-only execution,
resource-management, and audit boundary.

## Contents

- [Validated authorization flow](#validated-authorization-flow)
- [Values the administrators must provide](#values-the-administrators-must-provide)
- [1. Publish protected-resource metadata](#1-publish-protected-resource-metadata)
- [2. Publish authorization-server metadata](#2-publish-authorization-server-metadata)
- [3. Configure Keycloak dynamic client registration](#3-configure-keycloak-dynamic-client-registration)
- [4. Apply audience and identity claims](#4-apply-audience-and-identity-claims)
- [5. Configure a pre-registered fallback client](#5-configure-a-pre-registered-fallback-client)
- [6. Configure DDAE authorization and MCP policy](#6-configure-ddae-authorization-and-mcp-policy)
- [7. Hand off to users](#7-hand-off-to-users)
- [8. Validate end to end](#8-validate-end-to-end)
- [9. Troubleshooting map](#9-troubleshooting-map)
- [10. Production operations](#10-production-operations)
- [Administrator handoff checklist](#administrator-handoff-checklist)

## Validated authorization flow

1. Codex contacts the DDAE MCP endpoint and discovers its protected-resource
   metadata.
2. The metadata identifies the OAuth resource and authorization-server issuer.
3. Codex reads the issuer's OpenID Connect metadata and dynamic-registration
   endpoint.
4. Codex dynamically registers a native public client for authorization code,
   PKCE S256, no token-endpoint authentication, and an exact loopback redirect.
5. The authorization server returns a generated `client_id` and no
   `client_secret`.
6. The user authenticates in the browser as their normal DDAE identity.
7. The authorization server returns a one-time code to Codex's loopback
   callback. Codex validates state and exchanges the code using the PKCE
   verifier.
8. The access token contains the audience required by DDAE. Codex initializes
   MCP and receives the tools authorized for that user.

The client always has a `client_id`. With DCR, neither the customer nor the
plugin needs to preconfigure it because the authorization server generates it.

## Values the administrators must provide

| Value | Example shape | Owner |
|---|---|---|
| MCP endpoint | `https://<ddae-host>/mcp` | DDAE administrator |
| Protected-resource metadata | `https://<ddae-host>/.well-known/oauth-protected-resource` | DDAE administrator |
| OAuth resource | Normally `https://<ddae-host>`; use the metadata value | DDAE and identity administrators |
| Issuer | `https://<identity-host>/auth/realms/<realm>` | Identity administrator |
| OIDC metadata | `<issuer>/.well-known/openid-configuration` | Identity administrator |
| Required DDAE audience | Product/deployment-specific; the validated pilot used `dv-sep-starburst-ui` | DDAE administrator |
| Allowed login scopes | `openid` plus `offline_access` when refresh is enabled | Identity administrator |

Use trusted certificates and hostname verification. Do not use `curl -k` in a
production validation procedure and do not distribute private CA material in
the plugin.

## 1. Publish protected-resource metadata

The DDAE origin must expose protected-resource metadata that identifies the
resource and the expected authorization-server issuer. Validate the live
document rather than copying an example:

~~~bash
export DDAE_ORIGIN="https://<ddae-host>"
curl --fail --silent --show-error \
  "$DDAE_ORIGIN/.well-known/oauth-protected-resource" \
  | python3 -m json.tool
~~~

Confirm that:

- `resource` identifies the DDAE protected resource when the field is present.
- `authorization_servers` contains the expected issuer and no unexpected
  realm.
- Any advertised scopes match the intended interactive user flow.
- The MCP endpoint challenges unauthenticated requests as an MCP protected
  resource rather than returning a general-purpose web UI.

## 2. Publish authorization-server metadata

Validate the issuer metadata:

~~~bash
export OAUTH_ISSUER="https://<identity-host>/auth/realms/<realm>"
curl --fail --silent --show-error \
  "$OAUTH_ISSUER/.well-known/openid-configuration" \
  | python3 -m json.tool
~~~

Confirm the document advertises the expected:

- `issuer`
- `authorization_endpoint`
- `token_endpoint`
- `registration_endpoint`
- authorization-code response and grant support
- `S256` in `code_challenge_methods_supported`

All endpoint hostnames must be reachable from the Codex runtime and use
certificates trusted there.

## 3. Configure Keycloak dynamic client registration

Configure the realm's client-registration policy to accept the following
native public profile:

~~~json
{
  "application_type": "native",
  "grant_types": ["authorization_code"],
  "response_types": ["code"],
  "token_endpoint_auth_method": "none",
  "redirect_uris": [
    "http://127.0.0.1:<port>/callback/<nonce>"
  ]
}
~~~

In Keycloak versions that expose these controls under **Realm Settings** >
**Client Registration** > **Client Registration Policies**, review the
**Anonymous Access Policies** for **Trusted Hosts**, **Allowed Client Scopes**,
and **Allow Default Scopes**. Labels and navigation can vary by Keycloak
version; validate the resulting registration rather than relying only on the
console state.

Required behavior:

- Anonymous DCR is available to the authorized Codex deployment, or the
  customer provides an equivalent supported registration mechanism.
- `Allowed Client Scopes` permits the minimal interactive scope set. The
  validated Codex invocation explicitly requested `openid` and added
  `offline_access` for refresh-token support.
- The Trusted Hosts registration policy accepts requests from approved Codex
  runtimes. In the pilot, `host-sending-registration-request-must-match=false`
  was required because the default policy rejected the registration source.
  Disabling that check broadly increases DCR exposure; in production, restrict
  the registration endpoint to approved networks and apply rate limits,
  monitoring, and client-lifecycle controls, or use a supported trusted-host
  policy that works for the deployment.
- Standard authorization code is enabled; implicit, password, service-account,
  and client-credentials grants are not enabled for DCR-created Codex clients.
- PKCE S256 is required.
- Each registration preserves the exact loopback redirect URI supplied by the
  client. `127.0.0.1` is the correct native-client callback host; it is not
  replaced by the DDAE hostname.
- The registration response contains a generated `client_id` and no
  `client_secret`.

Do not validate this flow using password or client-credentials grants. A
service-account token does not prove user-delegated login, user RBAC, loopback
redirects, or PKCE. Do not share a realm-admin password, admin token, user
password, authorization code, access token, or refresh token in documentation
or support tickets.

## 4. Apply audience and identity claims

DDAE must accept the access token's `aud` claim. A DCR-created client does not
automatically inherit a mapper that was attached only to a manually created
test client.

Create an audience mapper in a realm-level default client scope (or the
deployment's equivalent policy) and ensure it is applied automatically to every
new DCR client:

- Included audience: the value required by the installed DDAE deployment.
- Add to access token: enabled.
- Add to ID token: only if separately required; DDAE authorization relies on
  the access token.
- Scope assignment: default for new dynamically registered clients.

The validated pilot required the audience `dv-sep-starburst-ui`. Confirm that
value against the installed DDAE version and security configuration instead of
assuming it for every deployment.

Test with a newly registered client after changing the default scope. Success
with an older client that received a one-off mapper does not prove the default
works.

Also verify the identity and authorization claims consumed by DDAE:

- `preferred_username` or the configured subject claim identifies the user.
- The configured role claim contains only the roles intended for DDAE. The
  validated claim path can be `realm_access.roles`, but deployments can use a
  different mapper.
- `email` and `name` are optional unless the installed DDAE configuration or
  customer policy requires them.

Prefer a dedicated realm-default client scope for the DDAE audience and claim
mappers. Do not create or overload a client scope named `openid` merely because
`openid` appears in the authorization request; confirm the realm's existing
scope model and the claims DDAE actually consumes.

## 5. Configure a pre-registered fallback client

Use this only when the customer prohibits DCR. Create an
administrator-approved native public client with:

- Client authentication disabled and no client secret.
- Standard authorization-code flow enabled.
- Implicit, direct password, service-account, and client-credentials grants
  disabled.
- PKCE S256 required.
- Only the loopback redirects required by Codex. Prefer exact registered URIs
  when the callback port is fixed; if variable ports require a Keycloak pattern,
  keep it limited to loopback hosts and document the risk approval.
- The same default DDAE audience and identity-claim scopes used for DCR clients.

Provide users only the public `client_id` through the approved Codex
configuration. Do not create or distribute a client secret. Revalidate the
fallback independently because success with a DCR-created client does not prove
the pre-registered client's redirect, mapper, or scope assignments.

## 6. Configure DDAE authorization and MCP policy

The DDAE administrator must:

- Enable and license the native MCP server for the installed release.
- Configure JWT issuer, resource/audience validation, and trusted TLS
  consistently with the published metadata.
- Map the authenticated user to intended DDAE roles.
- Preserve catalog, schema, table, row, and column authorization.
- Configure query limits, timeouts, result limits, resource groups, and audit
  retention appropriate for interactive agent use.
- Keep DDL, DML, procedures, administrative SQL, and multi-statement execution
  blocked for the read-only MCP interface.
- Treat the live `tools/list` response as authoritative for each identity.

The validated Starburst Enterprise MCP Server 1.0.0 identity advertised
`searchDataProducts` and `queryReadOnly`. Tool availability can differ by
release, license, configuration, and authorization.

## 7. Hand off to users

Give users:

- The complete `https://<ddae-host>/mcp` endpoint.
- Their expected DDAE roles and Data Product authorization boundary.
- The public `client_id` only when using the pre-registered fallback.
- The [DDAE OAuth user guide](DDAE_OAUTH_USER_GUIDE.md).

Do not give users a client secret or instruct them to copy tokens. Do not ask
them to configure an OAuth resource override when protected-resource discovery
is correct. If an override is unavoidable, validate that the authorization
request contains exactly one `resource` value.

## 8. Validate end to end

Use a normal, non-admin DDAE user. Retain redacted results and server audit
events, never credentials.

1. Confirm protected-resource and issuer discovery.
2. Confirm DCR creates a new public client with the exact loopback redirect,
   authorization code only, PKCE S256, and no secret.
3. Complete browser authentication and verify the token exchange succeeds.
4. Confirm the access token has the required DDAE audience without printing
   the token into the test transcript.
5. Initialize MCP and inspect the complete, pagination-aware `tools/list`
   response.
6. Call `searchDataProducts` with a deliberately narrow no-match term to prove
   authenticated tool execution without retrieving customer data.
7. With approval, call `queryReadOnly` once using this synthetic statement:

   ~~~sql
   SELECT 1 AS validation_value LIMIT 1
   ~~~

   Expect one integer column, one row containing `1`, and a query ID. Confirm
   DDAE recorded the user and query in its audit trail.
8. Verify refresh after the initial access-token lifetime without displaying or
   manually copying any token.

## 9. Troubleshooting map

| Symptom | Likely cause | Administrator action |
|---|---|---|
| DCR returns `403 insufficient_scope` and names `Allowed Client Scopes` | Registration policy rejects `openid`, refresh scope, or a default scope | Permit only the approved interactive scope set and retest with a new registration. |
| DCR is rejected by `Trusted Hosts` | Registration source does not satisfy the Keycloak host policy | Configure approved hosts if supported; otherwise use narrowly scoped network controls before disabling the match check. |
| Authorization returns `invalid_request: duplicated parameter` | `resource` was supplied by discovery and by a manual override | Remove the redundant override and verify exactly one `resource` value. |
| Redirect URI is invalid | Exact loopback URI was not registered or native-client redirects are blocked | Accept the DCR-supplied `http://127.0.0.1:<port>/callback/<nonce>` URI and require PKCE S256. |
| Token exchange returns `invalid_grant: Code not valid` | Code expired, was already used, callback state does not match, or PKCE verifier differs | Start a new login. Do not reuse or expose the old code. Check authorization-server events. |
| Login succeeds but MCP returns `401` or rejects audience | Audience mapper did not apply to the newly registered client | Attach the required audience through a default client scope and retest with a new client. |
| Login succeeds but the user or roles are wrong | Identity or role mapper does not match DDAE's configured claim paths | Verify the configured subject, username, and role claims with a new public client; do not add unrelated claims. |
| MCP initializes but tools or data are absent | DDAE role or product authorization differs for the user | Verify intended RBAC; do not broaden permissions merely to make a test pass. |
| Refresh fails after the access token expires | `offline_access`, refresh-token policy, or the DCR client lifecycle is incorrect | Verify approved refresh scope, token policy, and that the active DCR client was not deleted. |

## 10. Production operations

- Restrict DCR and OAuth endpoints to intended networks and clients while
  preserving standards compatibility.
- Rate-limit and monitor client registrations, authorization failures, token
  exchanges, refreshes, MCP initialization, and queries.
- Establish retention and cleanup for abandoned DCR test clients. Do not delete
  an active client while its users still depend on its refresh tokens.
- Revoke the client and its grants when a deployment is retired or compromised.
- Use normal user identities, least-privilege DDAE roles, and documented
  offboarding.
- Rerun discovery, DCR, audience, refresh, tool-catalog, and bounded-query tests
  after Keycloak, DDAE, TLS, ingress, or Codex changes.
- Preserve OAuth and DDAE audit records according to customer policy without
  logging raw tokens or authorization codes.

## Administrator handoff checklist

- [ ] Protected-resource metadata names the correct resource and issuer.
- [ ] Issuer metadata advertises authorization, token, registration, and PKCE
      S256 support.
- [ ] DCR accepts the native public authorization-code profile and returns no
      secret.
- [ ] `Allowed Client Scopes` permits the approved minimal scope set.
- [ ] Trusted Hosts policy and compensating network controls are approved.
- [ ] A default client scope adds the required DDAE audience to every new DCR
      client.
- [ ] Username and role claims match DDAE's configured claim paths.
- [ ] Exact loopback redirects are accepted and PKCE S256 is required.
- [ ] If DCR is prohibited, the fallback public client has no secret and has
      been validated independently.
- [ ] DDAE JWT, RBAC, row/column controls, query limits, and audit are enabled.
- [ ] A non-admin identity can initialize MCP and sees only its expected tools.
- [ ] No-match discovery and the synthetic bounded query pass.
- [ ] Token refresh and DCR-client lifecycle procedures are validated.
