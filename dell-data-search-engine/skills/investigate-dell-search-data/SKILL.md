---
name: investigate-dell-search-data
description: Investigate governed Dell Data Search Engine data across indices, observability, security, cases, workflows, and knowledge tools. Use for explicit DDSE, Elastic, Elasticsearch, Kibana, index or index-pattern, ES|QL, APM, log, trace, host, alert, case, or Security Labs requests. Do not use for DDAE, Starburst, Trino, or governed Data Product discovery.
---

# Investigate Dell Search Data

Treat live `tools/list` as authoritative, but do not treat every advertised tool
as part of the default initial-review path. Choose the smallest eligible set
that answers the user's outcome.

Keep the product boundary explicit. An index name or index pattern is a DDSE
request, not a DDAE Data Product request.

## Route the request

For the Codex-first initial review, the default deterministic surface is limited
to index listing, mapping, exact ES|QL, known-document retrieval, service and
dependency lookup, trace metrics, correlated logs, log-rate analysis, and log
categories. Do not invoke other advertised tools in an initial-review case
unless the case explicitly declares and validates the corresponding dependency.

- For index discovery, schema inspection, exact ES|QL, or a known document, use
  the applicable `platform_core_list_indices`,
  `platform_core_get_index_mapping`, `platform_core_execute_esql`, or
  `platform_core_get_document_by_id` tool.
- For natural-language search, ES|QL generation, or index selection, use
  `platform_core_search`, `platform_core_generate_esql`, or
  `platform_core_index_explorer` only after their configured model dependency
  has been positively validated in the current environment. Do not probe these
  tools when deterministic listing or mapping can answer the request. Report a
  prerequisite failure instead of inventing a query.
- For cases or workflow status, use `platform_core_cases` or
  `platform_core_get_workflow_execution_status` only for retrieval supported
  by the advertised schema. Do not infer a write capability from the name.
- For alerts, logs, services, dependencies, traces, hosts, indices, anomaly
  jobs, or change points, select the matching `observability_*` retrieval or
  analysis tool and provide the required entity and time boundaries.
- For product or integration knowledge, use the matching
  `platform_core_product_documentation` or
  `platform_core_integration_knowledge` tool and report missing installed
  content or model artifacts directly.
- For Security Labs content or security alerts, use
  `security_security_labs_search` or `security_alerts` and preserve the
  current Kibana space, data, license, and privilege boundaries.

## Follow the investigation workflow

1. Clarify the entity, index or solution area, time range, and desired outcome
   when the request is ambiguous. For a broad prompt such as "check the
   infrastructure," ask for the environment, entity, measure, and time range
   before any tool call; do not infer them from available fixture context.
2. Confirm the needed tool is advertised and note its documented product,
   data, model, license, or content prerequisites.
3. Use `platform_core_list_indices` first when the request asks to find an
   authorized index. Inspect its mapping before exact ES|QL when the schema is
   unknown. Do not list all indices when one named authorized index suffices.
   If deterministic discovery returns no matching index, stop and report the
   missing fixture or authorization boundary; do not guess another index name
   or fall back to a model-dependent discovery tool.
4. Use explicit fields, filters, time ranges, aggregation boundaries, and
   result limits. Avoid broad exports and unnecessary source fields.
   When the request names the evidence types or tools to use, stop after that
   path and do not repeat calls, add comparison windows, or broaden into other
   Observability tools unless the user asks.
5. Correlate only when the returned identifiers and timestamps support it.
   Distinguish observed evidence from an inferred explanation.
6. Preserve empty results, unsupported-feature errors, and authorization
   denials. Do not switch tools merely to bypass a boundary.
7. Report the finding with the index, document, alert, service, trace, case,
   workflow, or knowledge identifiers actually returned.

## Preserve boundaries

- Do not invoke unadvertised tools or construct undocumented parameters.
- Do not create or update cases, workflows, rules, connectors, indices, or
  documents unless a separately reviewed write tool is advertised and the
  user explicitly authorizes that action.
- When only retrieval tools are advertised and a user asks for a write, state
  that the operation is unavailable in the read-only surface. Do not ask for
  write details or imply that the operation can proceed later.
- Do not expose authorization headers, hidden/system indices, raw secrets,
  unnecessary personal data, or verbose backend errors.
- Treat missing Agent Builder, ML, content, licensing, or solution data as a
  prerequisite gap—not permission to fabricate results.

## Format the answer

Return:

1. **Finding** — concise answer and confidence.
2. **Evidence** — returned source identifiers, timestamps, status, and relevant
   values.
3. **Method** — tools used and scope applied.
4. **Limitations** — missing data, prerequisites, truncation, or permissions.

For security or operational investigations, label hypotheses explicitly and
avoid presenting correlation as causation.
