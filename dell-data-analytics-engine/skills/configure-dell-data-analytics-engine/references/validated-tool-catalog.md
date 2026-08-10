# Validated DDAE MCP catalog

## Observed deployment

Observed August 10, 2026 from `Starburst Enterprise MCP Server 1.0.0` using MCP
protocol 2025-03-26. A refreshed, pagination-aware audit returned one page,
exactly two tools, and no `nextCursor`. This is complete for the tested endpoint
and OAuth identity, not for every DDAE release or configuration.

| Tool | Input | Purpose | DDAE-hosted LLM | Validation |
|---|---|---|---|---|
| `searchDataProducts` | Required `query` search text | Search governed Data Products visible to the authenticated user. | Not required. | Authenticated no-match call returned HTTP 200 with `isError=false`. |
| `queryReadOnly` | Required `query` containing Trino SQL | Execute one read-only query under DDAE/Starburst authorization. | Not required; Codex may formulate SQL. | Discovered and schema-validated; execute only against an approved dataset with a bounded query. |

## Documented Starburst Enterprise surface

Current Starburst Enterprise documentation describes the following product
surface. Confirm availability against documentation for the customer's exact
release and the live `tools/list` response.

| Tool or capability | Availability | Purpose or prerequisite |
|---|---|---|
| `queryReadOnly` | Built-in | Execute synchronous read-only Trino SQL under result-size, timeout, resource, and access controls. |
| `searchDataProducts` | Built-in when Data Products are available | Search Data Product names, summaries, descriptions, views, and materialized views. |
| `getDataProductDetails` | Built-in when supported and Data Products are available | Retrieve a Data Product's governed metadata, views, materialized views, and columns. |
| `listParametrizedQueryTools` | Optional | List administrator-defined query templates; requires `mcp.parametrized-tools.enabled=true` and definition paths. |
| `parametrizedQuery` | Optional | Execute an approved parameterized query with server-side typing and validation. |
| Starburst AI data-product agent tool | Optional and model-dependent | Requires Data Products and a configured `mcp.tool.data-product-agent.model-id` alias. |

Parameterized-query definitions can add governed business capabilities behind
the optional parameterized tool interface. Installed release, MCP license,
Data Products, feature flags, definition files, model aliases, and user
authorization can all affect the live catalog.

OAuth, DDAE roles, catalog permissions, row and column controls, resource
groups, query limits, and audit policy remain authoritative.
