# Validated DDAE MCP catalog

Observed August 10, 2026 from `Starburst Enterprise MCP Server 1.0.0` using MCP
protocol 2025-03-26. A refreshed, pagination-aware audit returned one page,
exactly two tools, and no `nextCursor`. This is complete for the tested endpoint
and OAuth identity, not for every DDAE release or configuration.

The table combines the live pilot result with the broader tool surface in the
current Starburst Enterprise documentation. The live `tools/list` response is
authoritative for a specific endpoint and identity.

| Tool | Pilot status | Purpose | Enablement or prerequisite | Validation |
|---|---|---|---|---|
| `searchDataProducts` | Enabled and advertised | Search governed Data Products visible to the authenticated user. | Built in when Data Products are available; requires OAuth and Data Product authorization. | Authenticated no-match call returned HTTP 200 with `isError=false`. |
| `queryReadOnly` | Enabled and advertised | Execute one synchronous read-only Trino SQL query. | Built in; subject to OAuth, data authorization, result-size, timeout, and resource controls. | `SELECT 1 AS validation_value LIMIT 1` completed successfully with one integer column, one row, structured content, and a query ID. |
| `getDataProductDetails` | Documented, not advertised by pilot | Retrieve a Data Product's metadata, views, materialized views, and columns. | Built in when supported by the installed release and Data Products are available; requires authorization to the Data Product. | Not available in the tested identity's live catalog. |
| `listParametrizedQueryTools` | Not enabled or not exposed by pilot | List administrator-defined parameterized query templates. | Administrator must set `mcp.parametrized-tools.enabled=true`, configure definition paths, add approved YAML definitions, and restart. | Not available in the tested identity's live catalog. |
| `parametrizedQuery` | Not enabled or not exposed by pilot | Execute an approved parameterized query with server-side typing and validation. | Same administrator configuration and restart as `listParametrizedQueryTools`; the user also needs authorization to referenced data. | Not available in the tested identity's live catalog. |

Parameterized-query definitions add governed business capabilities behind the
optional parameterized tool interface. DDAE validates typed inputs, resolves
approved table references, renders administrator-owned SQL templates, verifies
that the final statement is read-only, and executes it. This is safer and more
repeatable than allowing an agent to compose each complete SQL statement.

All definitions are loaded and validated at server startup, so every change or
new definition requires a restart. Installed release, MCP license, Data
Products, coordinator configuration, definition files, and user authorization
can affect the live catalog.

No separate DDAE inference configuration is required for the two tools exposed
by the pilot. Codex supplies conversational reasoning and can formulate SQL;
DDAE executes governed operations.

## OpenAI metadata audit — August 12, 2026

A refreshed live audit confirmed both tools expose input and output schemas,
`readOnlyHint=true`, and `destructiveHint=false`. Both omit
`openWorldHint` and per-tool OAuth `securitySchemes`. The
`queryReadOnly` input schema exposes `query` but does not mark it required.
Before OpenAI review, correct those server-owned metadata gaps and rescan the
endpoint.

Safe execution passed for both tools: a deliberately no-match Data Product
search returned structured content without error, and
`SELECT 1 AS validation_value LIMIT 1` returned one column, one row, a query
ID, and structured content without reading customer data.

## OAuth and execution revalidation — August 17, 2026

The endpoint was revalidated through standards-based OAuth protected-resource
discovery and Keycloak dynamic client registration. Registration produced a
native public client for authorization code with PKCE S256, no client secret,
and an exact loopback callback. Browser authentication and the code exchange
completed successfully, and a new Codex session initialized the MCP server and
again advertised exactly `searchDataProducts` and `queryReadOnly`.

The successful authorization request contained one `resource` value. A prior
attempt with both discovered resource metadata and a manual resource override
contained two values and was rejected as a duplicated parameter. The validated
login command explicitly requested `openid`; Codex also requested
`offline_access` for refresh support. A no-scope attempt requested the issuer's
broader advertised scope set and was not used.

The newly authenticated identity then executed the synthetic bounded statement
`SELECT 1 AS validation_value LIMIT 1` through `queryReadOnly`. It returned
`validation_value=1`, one row, no error, and a query ID. No customer catalog,
table, or Data Product was read.

## Authenticated Codex revalidation — September 10, 2026

Codex completed protected-resource discovery, Keycloak dynamic client
registration, browser authorization, PKCE S256 code exchange, and MCP OAuth
without a client secret or manually copied token. Authenticated deferred catalog
discovery exposed exactly `searchDataProducts` and `queryReadOnly` to Codex.

The Codex-facing definitions contained output schemas but marked no input field
as required for either tool and did not surface `readOnlyHint`,
`destructiveHint`, or `openWorldHint`. This was not a retained raw,
pagination-aware `tools/list` response, so it is an actionable submission gate
requiring raw contract confirmation rather than conclusive proof that the
upstream server removed metadata observed in August.

Two bounded live calls then passed in the required order. A unique no-match
`searchDataProducts` query returned zero products and structured content.
`SELECT 1 AS validation_value LIMIT 1` returned integer value `1`, structured
content, and a DDAE query ID. No named Data Product, catalog, schema, table, or
customer record was accessed. Omitted-argument execution was intentionally not
attempted because an empty search could enumerate authorized Data Products.

The sanitized record is
[`evals/results/ddae-codex-initial-review-20260910/live-safe-smoke.json`](../../../../evals/results/ddae-codex-initial-review-20260910/live-safe-smoke.json).

OAuth, DDAE roles, catalog permissions, row and column controls, resource
groups, query limits, and audit policy remain authoritative.
