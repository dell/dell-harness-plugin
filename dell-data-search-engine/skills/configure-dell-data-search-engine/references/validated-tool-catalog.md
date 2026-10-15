# Validated DDSE MCP catalog

Observed August 10, 2026 from `elastic-mcp-server 0.0.1` using MCP protocol
2025-06-18. A refreshed audit on August 12 again returned one page and 25
tools. An authenticated September 10 audit of the pilot deployment again returned `elastic-mcp-server 0.0.1`, MCP protocol 2025-06-18, one
page, and the same 25 tools. Prefer the customer's current `tools/list`; release,
license, Kibana space, privileges, data, installed content, and model
configuration can change it.

## OpenAI metadata audit — September 10, 2026

All 25 tools expose input schemas. None exposes an output schema, per-tool
security scheme, `readOnlyHint`, `destructiveHint`, or `openWorldHint`. Their
current names and descriptions describe retrieval, search, analysis, or
execution-status operations; no create, update, or delete tool was observed.
That textual observation does not replace required server annotations.

The September contract audit authenticated successfully with the previously
enrolled API key and did not invoke any product tool or read customer data. A
separate sanitized execution smoke used only synthetic nonexistent names and an
index-free constant ES|QL expression. Index discovery, missing-index mapping,
and constant ES|QL execution succeeded; omission of the required mapping input
correctly returned a tool error. Latencies were 120 ms, 119 ms, 384 ms, and
130 ms respectively. Every response was a single text item without
`structuredContent`; raw tool content was neither printed nor retained.

The lab TLS chain was accepted by curl with system trust but required Python's
explicit legacy-CA compatibility mode because its CA certificate lacks the
key-usage extension required by newer OpenSSL strict validation. Keep chain and
hostname verification enabled; repair the CA chain before external review
rather than depending on compatibility mode.

Before OpenAI review, correct the native server metadata, rescan the endpoint,
and validate structured results against declared output schemas. Either
constrain the initial review surface or prove every advertised tool and product
dependency with stable fixtures.

The plugin-owned initial-review policy now selects 10 deterministic tools and
six positive plus three negative synthetic cases. Fifteen other advertised
tools are conditional and excluded from selected workflows. This is a routing
and evidence boundary only: it does not remove tools from Elastic's live
`tools/list`.

## No Agent Builder chat model required for core execution

| Tool | Purpose | Other prerequisites or evidence |
|---|---|---|
| `platform_core_get_document_by_id` | Retrieve a known document by index and ID. | Index `read`; not individually data-tested. |
| `platform_core_execute_esql` | Execute exact ES|QL and return tabular results. | Supply an approved bounded query; generation can separately require an LLM. |
| `platform_core_get_index_mapping` | Retrieve mappings for authorized indices. | Live-tested; requires `view_index_metadata`. |
| `platform_core_list_indices` | List authorized indices, aliases, and data streams. | Live-tested; returned 12 non-system indices. |
| `platform_core_cases` | Search and retrieve Elastic cases. | Requires Cases data, space access, licensing, and case privileges; review write actions. |
| `platform_core_get_workflow_execution_status` | Retrieve workflow state and output. | Requires a known execution ID and workflow visibility. |
| `observability_run_log_rate_analysis` | Compare log rates across time periods. | Requires Observability data and applicable AIOps capability. |
| `observability_get_anomaly_detection_jobs` | Retrieve anomaly-detection jobs and status. | Requires Elastic ML jobs, privileges, and licensing. |
| `observability_get_alerts` | Retrieve Observability alerts with supported filters. | Requires alert data, rules, and privileges. |
| `observability_get_log_categories` | Retrieve categorized log patterns. | Requires logs and Elastic categorization or ML capability. |
| `observability_get_services` | Retrieve APM services and details. | Requires APM data and Observability privileges. |
| `observability_get_downstream_dependencies` | Identify dependencies called by a service. | Requires APM topology and trace data. |
| `observability_get_correlated_logs` | Retrieve logs correlated with service or trace context. | Requires correlated logs, traces, and matching fields. |
| `observability_get_hosts` | Retrieve monitored hosts and host details. | Requires infrastructure metrics and privileges. |
| `observability_get_trace_metrics` | Retrieve trace latency, throughput, and failure metrics. | Requires distributed tracing or APM data. |
| `observability_get_log_change_points` | Detect statistically significant log changes. | Requires log data and change-point capability. |
| `observability_get_metric_change_points` | Detect statistically significant metric changes. | Requires metric data and change-point capability. |
| `observability_get_index_info` | Inspect Observability indices, fields, values, and ranges. | Requires authorized Observability indices and metadata/read privileges. |

## Agent Builder chat model required

These three tools are `not_selected` for the initial review. Enable them only
after a default Agent Builder chat connector is positively validated in the
same Kibana space.

| Tool | Purpose | Evidence |
|---|---|---|
| `platform_core_search` | Turn a natural-language request into Query DSL or ES|QL and execute it. | Missing default connector produced an undefined `connectorId` failure. |
| `platform_core_generate_esql` | Generate ES|QL from natural language. | Missing default connector produced an undefined `connectorId` failure. |
| `platform_core_index_explorer` | Infer relevant indices and mappings from natural language. | Missing default connector produced an undefined `connectorId` failure. |

## Conditional content, embedding, or product dependencies

| Tool | Purpose | Dependency or evidence |
|---|---|---|
| `platform_core_product_documentation` | Search installed Elastic product documentation. | Pilot reported documentation not installed; knowledge search can require installed search or embedding artifacts. |
| `platform_core_integration_knowledge` | Retrieve knowledge from installed integrations. | Pilot could not resolve the Elastic model-content host; requires approved egress and artifacts. |
| `security_security_labs_search` | Search installed Elastic Security Labs content. | Pilot reported content not installed; knowledge search can require search or embedding artifacts. |
| `security_alerts` | Search, filter, aggregate, and summarize Security alerts. | Requires Security data, space, licensing, and privileges; natural-language translation dependency needs release-specific validation. |

`No Agent Builder chat model required` does not mean `no dependency`.
Observability, Security, semantic search, and ML tools can still require their
own data, jobs, models, packages, licenses, egress, and privileges.
