# Capability catalog

Treat live `tools/list` as authoritative. The plugin launcher selects the focused
`plugin-v1` profile by default. The bundled server registers 70 tools
only when `all` is explicitly selected or no profile is supplied outside the
plugin launcher.

## Plugin v1 default profile

The default contains the 36 tools listed in `evals/cases/dataloop.json` under
`declared_tools`. They cover authorization discovery, bounded dataset
exploration, RAG, pipeline execution and recovery, PII assessment, image-classifier lifecycle, and exact cleanup.

## Discovery profile

Projects:

- `project_list`, `project_get`

Datasets and items:

- `dataset_list`, `dataset_get`, `dataset_create`, `dataset_update`,
  `dataset_delete`, `dataset_profile`
- `item_list`, `item_get`, `item_upload`, `item_update`

Annotations:

- `annotation_list`, `annotation_get`, `annotation_labels`,
  `annotation_create`, `annotation_delete`,
  `annotation_delete_by_model`

## Pipelines profile

Pipeline lifecycle:

- `pipeline_list`, `pipeline_get`, `pipeline_create`,
  `pipeline_create_from_template`, `pipeline_delete`, `pipeline_install`,
  `pipeline_pause`, `pipeline_reset`, `pipeline_stats`,
  `pipeline_update_settings`

Graph operations:

- `pipeline_node_add`, `pipeline_node_update`, `pipeline_node_remove`,
  `pipeline_connect_nodes`, `pipeline_disconnect_nodes`

Execution control:

- `pipeline_execute`, `execution_list`, `execution_terminate`,
  `pipeline_validate`

Triggers:

- `trigger_create`, `trigger_list`, `trigger_get`, `trigger_delete`

## Models profile

Marketplace and application lifecycle:

- `marketplace_search`, `marketplace_get_dpk`, `model_install`, `app_list`,
  `app_uninstall`

Models:

- `model_create`, `model_deploy`, `model_train`, `model_predict`,
  `model_set_subset`, `model_delete_subset`, `model_list`, `model_get`,
  `model_delete`

Embeddings:

- `dataset_embeddings_status`, `dataset_embed`, `dataset_cluster`,
  `dataset_visual_search`

PII:

- `pii_report`, `pii_set_findings`, `pii_writeback_code`, `pii_filter`

## RAG profile

- `rag_pipeline_create`, `rag_add_pdf_filter_node`,
  `rag_add_pdf_to_text_node`, `rag_add_text_to_chunks_node`,
  `rag_add_embedding_node`, `rag_add_output_node`, `rag_query`
- supporting pipeline, graph, and marketplace tools from the RAG profile

## Tool namespace

Clients expose this server as `dell_dataloop`; the concise raw tool names are
therefore scoped by the MCP server identity. Keep that namespace stable. Do not
rename tools merely to repeat the product name in every call.

## Safety contract

Read-only calls use read-only and idempotent MCP annotations. Mutating and destructive calls
are distinguished. Open-world access is marked false. The server validates
inputs and raises tool errors rather than returning an error-shaped success.

Long-running, billable, persistent, destructive, and generated-code operations
require a plan and explicit user confirmation. The server enforces confirmation
fields for critical operations, but the skill must still explain the action and
target before invoking them.

## Known constraints

- The `dtlpy` SDK uses one authenticated identity per process.
- The current integration uses API keys, not OAuth.
- The complete development surface is about 30,800 measured tokens. The
  focused 36-tool
  default is about 19,500 measured tokens with typed output contracts; use an
  even smaller profile when a
  deployment needs only one functional group.
- The focused tools have named, typed output contracts. Tools outside that
  surface remain outside the reviewed plugin profile and may still use generic output schemas.
- Marketplace search may scan a capped result set and locally filter it; inspect
  `scan_truncated` and do not infer global absence.
- ML pipeline nodes are not implemented because the installed SDK has no stable
  typed node contract.
- Training, deployment, inference, embedding, pipelines, and triggers require
  live validation against an approved disposable project.
