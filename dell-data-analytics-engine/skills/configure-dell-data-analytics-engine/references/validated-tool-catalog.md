# Validated DDAE MCP catalog

Observed August 10, 2026 from Starburst Enterprise MCP Server 1.0.0 using MCP
protocol 2025-03-26. Prefer the customer's live `tools/list`; release, license,
configuration, and policy can change the catalog.

| Tool | Input | Purpose | DDAE-hosted LLM | Validation |
|---|---|---|---|---|
| `searchDataProducts` | `query` search text | Search governed Data Products visible to the authenticated user. | Not required. | Authenticated no-match call returned HTTP 200 with `isError=false`. |
| `queryReadOnly` | `query` containing Trino SQL | Execute one read-only query under DDAE/Starburst authorization. | Not required; Codex may formulate SQL. | Discovered and schema-validated; execute only against an approved dataset with a bounded query. |

Both tools require OAuth. DDAE roles, catalog permissions, row and column
controls, resource groups, query limits, and audit policy remain authoritative.
