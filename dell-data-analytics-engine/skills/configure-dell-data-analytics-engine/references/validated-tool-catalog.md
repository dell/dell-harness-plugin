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
| `queryReadOnly` | Enabled and advertised | Execute one synchronous read-only Trino SQL query. | Built in; subject to OAuth, data authorization, result-size, timeout, and resource controls. | Discovered and schema-validated; execute only against an approved dataset with a bounded query. |
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

OAuth, DDAE roles, catalog permissions, row and column controls, resource
groups, query limits, and audit policy remain authoritative.
