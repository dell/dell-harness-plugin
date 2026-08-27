---
name: build-dataloop-rag-workflows
description: >
  Plan, build, validate, and query governed document RAG pipelines in Dell Data
  Orchestration Engine (Dataloop). Use for PDF ingestion, chunking, embeddings,
  searchable output datasets, RAG pipeline graphs, installation, or bounded
  semantic queries. Do not use for a generic local vector database request.
---

# Build Dataloop RAG Workflows

Use the `rag` profile, adding `discovery` when project or dataset discovery is
needed. Pipeline code and compute run in Dataloop, not in the local Codex
workspace.

## Plan before creating

Resolve the project and source dataset, then ask for any missing choices that
change behavior: source folder, PDF selection filter, chunk size and overlap,
embedding approach, output dataset, query limits, and retention expectations.
Present the proposed graph, compute implications, objects to be created, and
rollback path. Do not create anything until the user approves the plan.

## Build the graph

Use this order and preserve returned IDs:

1. `rag_pipeline_create`.
2. `rag_add_pdf_filter_node` with a bounded DQL filter.
3. `rag_add_pdf_to_text_node`.
4. `rag_add_text_to_chunks_node`.
5. `rag_add_embedding_node`.
6. `rag_add_output_node` targeting the approved dataset.
7. `pipeline_connect_nodes` for each intended edge.
8. Read the graph with `pipeline_get` before installation.
9. `pipeline_validate`; stop if the platform rejects the graph.
10. `pipeline_install` only after the user confirms the final graph and cost or
   persistence implications.

Generated code nodes require the server's explicit code confirmation. Show the
purpose and relevant behavior of generated code before setting
`confirm_code=true`. Do not insert arbitrary user-provided code without review.

## Validate and query

After installation, use `execution_list` and the approved output dataset to
check execution evidence. Use `pipeline_execute` only for an explicitly approved bounded run. Run
`rag_query` with a specific question and a small
result limit. Distinguish retrieved evidence from the model's interpretation;
do not invent citations or claim completeness.

Pause or reset a pipeline only after explaining the operational impact.
Deletion is destructive and requires an exact target plus explicit confirmation.

## Preserve boundaries

Honor project membership, dataset permissions, resource limits, and audit
controls. Do not broaden the source filter, ingest unrelated documents, expose
sensitive chunks, or start persistent compute implicitly.

## Format the answer

Include the approved design, created object IDs, graph verification, install or
execution status, query scope, grounded results, and any incomplete or failed
stage. State clearly when live execution was not performed.
