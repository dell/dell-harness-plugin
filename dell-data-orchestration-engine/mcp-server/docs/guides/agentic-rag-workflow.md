# Agentic PDF/RAG Workflow with doe-mcp

A conversational walkthrough for driving the `doe-mcp` MCP server: discover datasets, build a RAG
pipeline with the direct RAG tools (`rag_pipeline_create`, `rag_add_pdf_filter_node`,
`rag_add_pdf_to_text_node`, `rag_add_text_to_chunks_node`, `rag_add_embedding_node`,
`rag_add_output_node`, `rag_query`), run it, and iterate. Those tools filter PDFs with DQL and run
everything expensive on DOE; marketplace templates remain a fallback. Two storage facts shape
the whole workflow: **each chunk is its own item** in the output dataset, and **embeddings live in
the embedding model's feature set**, never in item metadata.

The workflow is deliberately **human-gated**. Phase 0 is a planning conversation in which nothing is
executed; the agent only starts calling mutating tools once you approve the plan. Read-only
discovery is allowed during planning, so the plan is grounded in what actually exists.

Works with any MCP client — Devin, Claude Code or Desktop, and Codex have all been used against
this server. Nothing below is client-specific; see the README for per-client setup.

Requirements and recommendations use RFC-2119 language.

Each phase below is a dialogue. **You** lines are what you type; **Agent** lines are the expected
shape of the reply, not verbatim output. The agent chooses the arguments; you approve the intent.
Exact arguments live in [Appendix A](#appendix-a-tool-reference), and the rules that hold for the
whole session are listed once under [Guardrails the agent MUST
respect](#guardrails-the-agent-must-respect) — read those first.

## Phase 0 — Agree the plan before anything runs

This is the gate. It turns a vague request into an explicit, approved plan, and surfaces capability
gaps *before* you have half a pipeline on the platform.

Configure `doe-mcp` in your MCP client as described in the repository README under **Deploy with MCP
clients**, then start a fresh session so the tools load. The server MUST receive `DATALOOP_API_KEY`
and `DATALOOP_PATH`; `DATALOOP_PATH` is a private local **directory** for dtlpy state, not a URL.
Against anything other than the DOE cloud, the server MUST also receive `DATALOOP_GATE_URL` —
the API base of that deployment's gate, not its web console — plus `DATALOOP_VERIFY_SSL=false` if
the instance serves a self-signed certificate chain. If the first read-only call fails with an
authorization or routing error rather than an empty result, suspect the gate URL before the API key:
a console host answers with HTML, an unrouted host answers with a gateway `no Route matched` error.
The gate you want is the host named in the `aud` claim of the API key.

- **You:** I want to build a RAG setup on DOE from my PDFs, using the doe-mcp tools. Stay in
  planning mode: read-only tools are fine — use them to ground the plan — but do not create, update,
  delete, install or deploy anything until I say "approved". Tell me which parts of this the server
  supports directly and which it doesn't. Call project_list, then for the project I pick show the
  datasets, any pipelines that already exist, and the models already installed. Also search the
  marketplace for RAG pipeline templates with templates_only=true. Then give me a plan.

- **Agent:** States the capability boundaries below, returns tables of datasets, pipelines and
  installed models plus any template packages found, then proposes a phased plan.

The agent SHOULD use only these tools during Phase 0: `project_list`, `project_get`, `dataset_list`,
`dataset_get`, `marketplace_search`, `marketplace_get_dpk`, `app_list`, `model_list`, `model_get`,
`pipeline_list`, `pipeline_get`, `pipeline_stats`. It MUST make these limits explicit while
planning, because they change the plan's shape:

| What you probably want | Supported? | Consequence for the plan |
| --- | --- | --- |
| Discover datasets and pick one | Yes | Straightforward |
| Create a RAG pipeline with PDF filtering | **Yes** | Use `rag_pipeline_create` + `rag_add_*_node` tools (Phase 3.5) |
| Filter PDFs using DQL | Yes | `rag_add_pdf_filter_node` uses DQL automatically |
| Extract text from PDFs | Yes | `rag_add_pdf_to_text_node` writes a `.txt` item into the output dataset |
| Split text into chunks | Yes | `rag_add_text_to_chunks_node` writes one chunk **item** per chunk |
| Generate embeddings | Yes | `rag_add_embedding_node`, but only with a model already installed **and deployed**; billable |
| Query processed chunks | Yes | `rag_query` is vector search and needs the same `model_id` that embedded the chunks |
| Inspect the derived text and chunk items | Yes | `item_list` / `item_get` on the output dataset (`/rag/text`, `/rag/chunks`) |
| Check embedding progress | Yes | `dataset_embeddings_status` reports the output dataset's feature sets |
| Install a RAG pipeline template (fallback) | Yes | `pipeline_create_from_template`, gated by `confirm` |
| Reuse models already installed | Yes | `app_list` + `model_list`; avoids paying for a fresh install |
| Drop a model straight into a pipeline | **No** | ML nodes have no SDK class; a deployed model is reached through a `function` node, or from a code node — which is how `rag_add_embedding_node` calls `model.embed` |
| Watch per-item progress | **No** | Only `pipeline_stats` counters and dataset counts |

Underlying detail the agent SHOULD be able to explain if asked:

- **DQL filtering is built in.** `rag_add_pdf_filter_node` filters for `metadata.system.mimetype ==
  "application/pdf"`.
- **Derived text and chunks are items, not metadata.** `rag_add_pdf_to_text_node` uploads each PDF's
  text as a `.txt` item under `/rag/text` in the output dataset; `rag_add_text_to_chunks_node`
  uploads one item per chunk under `/rag/chunks`. A chunk has to be an item because a DOE
  feature vector attaches to an entity — item, annotation or dataset — so chunk-level retrieval is
  impossible otherwise. `metadata.user.rag` on those items carries provenance only: `role`,
  `text_item_id`, `source_item_id`, `source_item_name`, `chunk_index`, `start_pos`, `end_pos`.
- **Embeddings live in a feature set, never in metadata.** `rag_add_embedding_node` calls
  `model.embed(item_ids=...)` on the chunk items in batches of 100; the model's own service writes
  each vector into its own feature set. Nothing copies a vector into `metadata.user`, so progress is
  visible through `dataset_embeddings_status`, not by reading items.
- **The embedding model must already be deployed.** `rag_add_embedding_node` takes a `model_id`, not
  a model name, and `dtlpy`'s `model.embed` refuses a model with no running service. Find a deployed
  one with `model_list` before planning this stage.
- **Every RAG code node needs an `output_dataset_id`.** The nodes upload the items they produce
  themselves, so `rag_add_pdf_to_text_node`, `rag_add_text_to_chunks_node` and
  `rag_add_embedding_node` each require the output dataset explicitly.
- **Failures surface as failed pipeline executions.** A PDF that yields no text, an empty text item,
  or a document with no chunk items raises inside the node; nothing is recorded in item metadata for
  you to go looking for.
- **There is no `dataset_clone`,** so a filtered copy of a dataset is still a pipeline. Item-level
  tools (`item_list`, `item_get`, `item_upload`, `item_update`) do exist and are how you inspect
  what the pipeline produced.
- **No active-project concept.** Every tool takes an explicit `project_id`.
- **Pipeline templates remain a fallback.** Templates are raw pipeline JSON on
  `components.pipeline_templates`; `pipeline_create_from_template` validates that JSON with
  `pipelines.validate` before asking for confirmation.
- **Template packages ship no model components,** so `marketplace_search` needs
  `templates_only=true` to find them; the `models_only` default would hide every one.

- **Agent:** Plan, using the direct RAG tools: create pipeline `rag-pipeline` over the source and
  output datasets; add the DQL PDF filter node; add the PDF-to-text node writing `.txt` items into
  `<output_dataset>/rag/text`; add the text-to-chunks node at chunk_size=512, chunk_overlap=50,
  writing chunk items into `<output_dataset>/rag/chunks`; add the embedding node using
  already-deployed model `<model_id>`, whose service writes the vectors into its feature set
  (billable); add the output storage node terminating on the output dataset; connect the nodes in
  sequence; install and report counters. Alternatively, with marketplace templates: create dataset
  `<source>-pdf-only`; create pipeline `pdf-filter-copy` (source dataset → PDF filter code node →
  new dataset); create `rag-preprocessing` and `rag-query` from template `<name>`, reusing installed
  model `<model>`; rewire the templates' ingest nodes to the PDF-only dataset; run, then report
  counters. Nothing is created yet — reply "approved", or tell me what to change.

Before approving, you SHOULD require the agent to have shown you: the **exact PDF-filter code** it
intends to deploy; the **template's `node_names` and `node_types`**, from `marketplace_get_dpk`;
which **deployed model** it will embed with, by `id`, and the cost of running it over every chunk;
the **output dataset** every RAG code node will write text items, chunk items and vectors into; and
the **names** of every object it will create. Approve as narrowly as you like — partial approval is
the recommended default:

- **You:** Approved for step 1 and 2 only. Pause and check with me again before you touch the
  templates.

## Phase 1 — Discover and choose a dataset

- **You:** Show the datasets as a numbered table with name, id and items_count, and tell me which
  look like document sets. Use number 3 as the source.

- **Agent:** Presents the table, paging with `has_more` if needed, confirms the choice by name *and*
  id, calls `dataset_get` to verify it resolves, and reports the current item count. The agent MUST
  NOT proceed until exactly one source dataset is confirmed.

## Phase 2 — Create the PDF-only dataset

- **You:** Create the filtered dataset. Call it `<source>-pdf-only`.

- **Agent:** Calls `dataset_create`, reports the new `id`, and reminds you it is empty — a filtering
  pipeline still has to populate it.

## Phase 3 — Filter PDFs into it

There is no clone-with-filter operation, so this is a real pipeline.

- **You:** Build the filter pipeline. Show me the filter code before you add it.

- **Agent:** Proposes `pdf-filter-copy` with three nodes and shows the code node source for review:

  ```python
  def keep_pdfs(item):
      name = (item.name or '').lower()
      mimetype = ((item.metadata or {}).get('system', {}) or {}).get('mimetype', '') or ''
      is_pdf = name.endswith('.pdf') or 'pdf' in mimetype.lower()
      if not is_pdf:
          return None
      return item
  ```

Review that code properly. It runs on DOE's runners, which is why `pipeline_node_add` requires
`confirm_code: true`. Its correctness depends on how your platform populates `item.name` and
`metadata.system.mimetype`, and a wrong predicate silently copies nothing, or everything.

- **You:** Code looks right. Add the nodes, wire them up, show me the graph, then run it on a small
  dataset first.

- **Agent:** Adds the source dataset node with `load_existing_data: true`, the code node and the
  target dataset node; connects them; calls `pipeline_get` with `include_graph: true` and shows the
  result; then, after you approve the graph, calls `pipeline_install` and reports `pipeline_stats`
  counters and the target dataset's `items_count`.

## Phase 3.5 — Build the RAG pipeline with the direct RAG tools (recommended)

The preferred path for most RAG use cases; no marketplace template is involved.

- **You:** Build a RAG pipeline using the direct RAG tools. Show me the pipeline structure before
  creating nodes.

- **Agent:** Proposes the structure: 1. `rag_pipeline_create` creates the pipeline. 2.
  `rag_add_pdf_filter_node` filters the source dataset to PDFs using DQL. 3.
  `rag_add_pdf_to_text_node` extracts each PDF's text and uploads it as a `.txt` item under
  `/rag/text` in the output dataset. 4. `rag_add_text_to_chunks_node` splits that text item into one
  chunk item per chunk under `/rag/chunks`, size and overlap configurable. 5.
  `rag_add_embedding_node` sends the document's chunk items to a deployed model, whose service
  writes the vectors into its feature set. 6. `rag_add_output_node` terminates the graph on the
  output dataset. 7. `pipeline_connect_nodes` wires the nodes together.

- **You:** Looks good. Create the pipeline and add the nodes. What are the default chunk settings,
  and which model will you embed with?

- **Agent:** Calls `rag_pipeline_create`, then each `rag_add_*_node` in sequence, showing the
  configuration of each; all three code nodes are given `output_dataset_id` explicitly, because each
  uploads the items it produces itself. Reports the chunk defaults — chunk_size=512 characters,
  chunk_overlap=50 — and that overlap preserves context at chunk boundaries, with each chunk
  becoming its own item carrying `chunk_index`, `start_pos` and `end_pos` so a retrieved chunk can
  be located in the source document. Calls `model_list`, shows each model's `name`, `id` and
  `status`, and proposes one that is already **deployed**: `model.embed` fails on a model with no
  service, so an installed-but-undeployed model is not usable here. Notes that embedding runs the
  model over every chunk item and is therefore billable.

### One-call alternative: rag_pipeline_build

`--profile skills` offers `rag_pipeline_build`, which takes the same choices — source dataset,
output dataset, embedding model, chunk size, chunk overlap — and builds the whole five-node graph in
one call.

It is deterministic: the same arguments produce the same graph, node ids included, verified against
the platform. It writes the graph in one platform call instead of eleven, which removes most of the
concurrent-edit risk described elsewhere in this guide.

The step-by-step `rag_add_*` path here remains correct, and is what the default tool surface offers.

- **You:** Use chunk_size=256 for finer granularity. Then wire up the nodes and show me the graph
  before running.

- **Agent:** Updates the `rag_add_text_to_chunks_node` call with chunk_size=256, then calls
  `pipeline_connect_nodes` to wire pdf_filter → pdf_to_text → text_to_chunks → embedding →
  output_storage — four calls, because a five-node chain needs four edges — then calls
  `pipeline_get` with `include_graph: true` and shows the result.

- **You:** Run it, then show me what actually landed in the output dataset and the state of the
  embeddings.

- **Agent:** Calls `pipeline_install` and reports `pipeline_stats` counters and the output dataset's
  `items_count`. Calls `item_list` on the output dataset with `name: "/rag/text/*"` and again with
  `name: "/rag/chunks/*"`, paging while `has_more` is true, and shows a few chunk items with their
  `metadata.user.rag` provenance (`text_item_id`, `source_item_name`, `chunk_index`). Then calls
  `dataset_embeddings_status` and reports each feature set's `id`, `name`, `vector_count` and
  `model_id`.

Embedding is asynchronous, so `vector_count` climbing towards the chunk-item count is the progress
signal — **not** `size`, which is the width of one vector and never changes;
no feature set at all means nothing has been embedded yet. Nothing is written to item metadata by
the embedding node, so the feature set is the only store. A failed extraction, an empty text item or
a document with no chunk items raises inside its node and shows up as a failed execution in
`pipeline_stats`, not as an error field in metadata.

- **You:** Search the processed chunks for relevant information.

- **Agent:** Calls `rag_query` against the output dataset with the same `model_id` used to embed,
  and returns the ranked chunks.

`rag_query` is vector search, not keyword matching. It embeds your query with that model — uploading
it as a throwaway text item under `/rag/queries`, waiting for the vector, then deleting the item —
and ranks the chunk vectors by cosine similarity in the server, because `dtlpy` exposes no
vector-distance query. Consequences worth knowing:

- `model_id` MUST be the model that embedded the chunks; vectors from another model are not
  comparable.
- `feature_set_id` is optional and defaults to the model's feature set, or to the dataset's only
  one. If the dataset has several and none is the model's, the tool asks you to name one.
- Ranking runs over a bounded sample — `max_vectors` defaults to 1000 and caps at 2000. Check
  `scan_truncated` before treating the top result as the best in the dataset.
- `max_results` defaults to 5 and `min_similarity` to 0.0.
- A dataset with no feature set, or no usable vectors, is an error rather than an empty result:
  nothing has been embedded yet.

Each result carries `item_id`, `item_name`, `similarity`, `source_item_id`, `source_item_name`,
`chunk_index` and the chunk's `text`, so an answer can quote the chunk and name the PDF it came
from.

## Phase 4 — Stand up RAG pipelines from marketplace templates (fallback)

Ask the agent to re-confirm before it starts, per your partial approval in Phase 0.

- **You:** Search the marketplace for RAG pipeline templates and show me what each package provides.
  Also tell me what models are already in this project, before installing anything new.

- **Agent:** Calls `marketplace_search` with `templates_only: true`, then `marketplace_get_dpk` on
  the candidates, listing each template's `name`, `description`, `node_count`, `node_names` and
  `node_types`. Then calls `app_list` and `model_list`, shows each model's `name`, `id` and
  `status`, and asks whether the template should be wired to one of them.

Template availability depends on what is published to your organisation: a template such as *RAG
Preprocessing Template* may simply not exist here. Reusing an installed model avoids the time and
cost of `model_install` plus `model_deploy`.

- **You:** Create both templates. Reuse the existing embedder. Names: `rag-preprocessing` and
  `rag-query`. Then show me each graph and point the ingest node at the PDF-only dataset.

- **Agent:** For each template, calls `pipeline_create_from_template` with `confirm: true`,
  reporting that the template validated against the platform first and that the pipeline was created
  but **not** started. Then calls `pipeline_get` with `include_graph: true`, names the placeholder
  nodes and ports, and proposes the rewiring before doing it.

Notes the agent SHOULD relay: `template_name` may be omitted only when a package provides exactly
one template, otherwise the tool lists the choices and stops; omitting `name` inherits the
template's own name; the response includes the created graph, a `template` summary, and the platform
`validation` result. A template arrives with placeholder wiring and is not useful until it points at
real resources. Rewiring usually means adding a correctly targeted `dataset` node, connecting it,
then removing the placeholder with `pipeline_node_remove` and `confirm: true` — which also deletes
every edge touching that node. To use an installed model, add a `function` node carrying its
deployed `service_id`.

## Phase 5 — Fallback: build the RAG pipeline by hand

Use this only when neither the direct RAG tools nor marketplace templates fit. There is no RAG node
type, so the flow is assembled from code nodes plus optional model services.

- **You:** The direct RAG tools don't fit my use case. Propose a hand-built pipeline instead, and
  confirm the stages with me before creating nodes.

- **Agent:** Proposes stages — PDF text extraction, chunking, embedding, custom processing — and
  waits.

Then, per stage, the agent adds a `code` node with `confirm_code: true`, keeping every import inside
the single top-level function. If a model is involved, it adds a `function` node with that model's
`service_id`; ML nodes remain unavailable. The agent MUST tell you that real PDF parsing and
vector-store writes need dependencies and credentials present on the DOE runner, and that any
placeholder extraction code MUST be replaced with an implementation you have approved.

## Phase 6 — Run

- **You:** Start both pipelines and report progress.

- **Agent:** Confirms each graph with `pipeline_get`, calls `pipeline_install`, then polls
  `pipeline_stats` and cross-checks destination dataset counts with `dataset_get`.

There is no per-item status tool, so counters plus dataset counts are the only progress signal. For
the embedding stage, add `dataset_embeddings_status` on the output dataset and watch the feature
set's `vector_count` (not `size` — that is the vector dimension, fixed at creation), since
embedding happens in the model's service after the node returns. If a previous
run left cycles behind, `pipeline_install` accepts `resume_option` with `terminateExistingCycles` or
`resumeExistingCycles`. `pipeline_execute` triggers a single run on demand, which is a cheaper way
to test a graph than installing it over a whole dataset. `execution_list` shows what is in flight,
and `execution_terminate` stops a run that has wedged.

## Phase 7 — Iterate with the human still in the loop

Every modification round repeats the small gate: propose, show, confirm, apply, re-inspect.

- **You:** Change the chunk size in the preprocessing pipeline, then re-run with clean counters.

- **Agent:** Pauses the pipeline with `pipeline_pause`, shows the current and proposed code, waits
  for approval, applies it with `pipeline_node_update` and `confirm_code: true`, then calls
  `pipeline_reset`, `pipeline_install`, and reports fresh `pipeline_stats`.

Re-chunking a document rewrites its chunk items by name (`<document>_chunk_00000.txt` upwards), so a
smaller chunk count than the previous run leaves the surplus chunk items — and their vectors —
behind in `/rag/chunks`, where `rag_query` will still rank them. The agent SHOULD list the
document's chunk items with `item_list` after a re-run and tell you if there are stale ones; there
is no item-delete tool, so removing them is a console job.

Available edits: rename or reposition a node with `pipeline_node_update`; replace code with
`pipeline_node_update` plus `confirm_code: true`; add a stage with `pipeline_node_add`; remove a
stage with `pipeline_node_remove` plus `confirm: true`, which removes its edges too; rewire with
`pipeline_connect_nodes`, and detach with `pipeline_disconnect_nodes` plus `confirm: true`. Graph
edits are serialized per pipeline, so concurrent edits to one pipeline will not interleave.

## Guardrails the agent MUST respect

1. Do not call any mutating tool before the Phase 0 plan is approved, and honour partial approvals
   exactly.
2. Never delete a dataset, pipeline, model or app without an explicit instruction; those tools also
   require `confirm: true`.
3. Never add or replace code-node source without showing the exact code first; `confirm_code: true`
   exists because that code runs on DOE runners.
4. Never instantiate a pipeline template without showing its `node_names` and `node_types` first.
5. Treat `model_install`, `model_deploy` and `model_train` as cost-incurring; prefer a model already
   listed by `model_list`. Installing a pipeline whose embedding node runs that model over every
   chunk item is cost-incurring too.
6. Validate the PDF filter on a small dataset before a large one.
7. Page through any list whose response reports `has_more: true`.
8. Report `scan_truncated` on filtered marketplace searches and on `rag_query` results.
9. Never assert that a named template exists until a search result shows it.
10. State unsupported requests plainly rather than silently substituting an alternative.

## Appendix A: tool reference

Read-only, safe during Phase 0 planning:

| Tool | Purpose |
| --- | --- |
| `project_list`, `project_get` | Resolve `project_id`; there is no ambient active project |
| `dataset_list`, `dataset_get` | Discover datasets and item counts |
| `marketplace_search` | Find packages; `templates_only: true` for pipeline templates |
| `marketplace_get_dpk` | Inspect `models` and `pipeline_templates` of one package |
| `app_list`, `model_list`, `model_get` | See what is already installed |
| `pipeline_list`, `pipeline_get`, `pipeline_stats` | Inspect existing pipelines and counters |
| `item_list`, `item_get` | Inspect the text and chunk items under `/rag/text` and `/rag/chunks` |
| `dataset_embeddings_status` | List a dataset's feature sets; how embedding progress is checked |
| `rag_query` | Vector search over a dataset's chunk items. Read-only in effect, though it does upload and then delete one throwaway query item, and running the model costs |

Mutating, allowed only after approval (Direct RAG tools — recommended):

| Tool | Gate |
| --- | --- |
| `rag_pipeline_create` | Plan approval; takes `source_dataset_id` and `output_dataset_id` |
| `rag_add_pdf_filter_node` | Plan approval; uses DQL to filter PDFs |
| `rag_add_pdf_to_text_node` | Plan approval; requires `output_dataset_id`; writes a `.txt` item under `/rag/text` |
| `rag_add_text_to_chunks_node` | Plan approval; requires `output_dataset_id`; configurable chunk size and overlap; writes chunk items under `/rag/chunks` |
| `rag_add_embedding_node` | Plan approval; requires `output_dataset_id` and a deployed `model_id`; the model's service writes the vectors, and running it is cost-incurring |
| `rag_add_output_node` | Plan approval; terminates the graph on the output dataset |

Mutating, allowed only after approval (Traditional pipeline tools):

| Tool | Gate |
| --- | --- |
| `dataset_create` | Plan approval |
| `dataset_update` | Patch keys limited to `description`, `metadata`; rename via `name` |
| `pipeline_create` | Plan approval |
| `pipeline_create_from_template` | `confirm: true`; validates the template first |
| `pipeline_node_add` / `pipeline_node_update` | `confirm_code: true` for code nodes |
| `pipeline_connect_nodes` | Plan approval |
| `pipeline_node_remove` / `pipeline_disconnect_nodes` | `confirm: true` |
| `pipeline_install` | `confirm: true`; keeps consuming compute until paused or deleted |
| `pipeline_pause` / `pipeline_reset` | Plan approval |
| `model_install` / `model_deploy` / `model_train` | `confirm: true` for raw overrides; cost-incurring |
| `dataset_delete` / `pipeline_delete` / `model_delete` / `app_uninstall` | `confirm: true` plus an explicit instruction |

Key argument shapes (Direct RAG tools):

```json
{
  "tool": "rag_pipeline_create",
  "arguments": {
    "project_id": "<project_id>",
    "name": "my-rag-pipeline",
    "source_dataset_id": "<source_dataset_id>",
    "output_dataset_id": "<output_dataset_id>",
    "description": "RAG pipeline for document processing"
  }
}
```

```json
{
  "tool": "rag_add_pdf_filter_node",
  "arguments": {
    "project_id": "<project_id>",
    "pipeline_id": "<pipeline_id>",
    "source_dataset_id": "<source_dataset_id>",
    "load_existing": true
  }
}
```

```json
{
  "tool": "rag_add_pdf_to_text_node",
  "arguments": {
    "project_id": "<project_id>",
    "pipeline_id": "<pipeline_id>",
    "output_dataset_id": "<output_dataset_id>"
  }
}
```

```json
{
  "tool": "rag_add_text_to_chunks_node",
  "arguments": {
    "project_id": "<project_id>",
    "pipeline_id": "<pipeline_id>",
    "output_dataset_id": "<output_dataset_id>",
    "chunk_size": 512,
    "chunk_overlap": 50
  }
}
```

```json
{
  "tool": "rag_add_embedding_node",
  "arguments": {
    "project_id": "<project_id>",
    "pipeline_id": "<pipeline_id>",
    "output_dataset_id": "<output_dataset_id>",
    "model_id": "<deployed_model_id>"
  }
}
```

```json
{
  "tool": "rag_query",
  "arguments": {
    "project_id": "<project_id>",
    "dataset_id": "<output_dataset_id>",
    "query": "What is the return policy?",
    "model_id": "<deployed_model_id>",
    "max_results": 5,
    "min_similarity": 0.0,
    "max_vectors": 1000
  }
}
```

A chunk item, as the chunking node writes it — provenance only, never text or vectors:

```json
{
  "name": "policy_chunk_00003.txt",
  "metadata": {
    "user": {
      "rag": {
        "role": "chunk",
        "text_item_id": "<text_item_id>",
        "source_item_id": "<pdf_item_id>",
        "source_item_name": "policy.pdf",
        "chunk_index": 3,
        "start_pos": 1386,
        "end_pos": 1898
      }
    }
  }
}
```

A `rag_query` response:

```json
{
  "query": "What is the return policy?",
  "dataset_id": "<output_dataset_id>",
  "feature_set_id": "<feature_set_id>",
  "vectors_scanned": 842,
  "scan_truncated": false,
  "results_count": 1,
  "results": [
    {
      "item_id": "<chunk_item_id>",
      "item_name": "policy_chunk_00003.txt",
      "similarity": 0.8117,
      "source_item_id": "<pdf_item_id>",
      "source_item_name": "policy.pdf",
      "chunk_index": 3,
      "text": "Returns are accepted within 30 days of purchase..."
    }
  ]
}
```

Key argument shapes (Marketplace templates):

```json
{"tool": "marketplace_search", "arguments": {"name": "rag", "templates_only": true, "page_size": 50}}
```

```json
{
  "tool": "pipeline_create_from_template",
  "arguments": {
    "project_id": "<project_id>",
    "dpk_name": "<dpk_name>",
    "template_name": "RAG Preprocessing Template",
    "name": "rag-preprocessing",
    "confirm": true
  }
}
```

```json
{
  "tool": "pipeline_node_add",
  "arguments": {
    "project_id": "<project_id>",
    "pipeline_id": "<pipeline_id>",
    "confirm_code": true,
    "node": {"node_type": "code", "name": "keep-pdfs", "position": [2, 1], "code": "<source>"}
  }
}
```

```json
{
  "tool": "pipeline_node_add",
  "arguments": {
    "project_id": "<project_id>",
    "pipeline_id": "<pipeline_id>",
    "node": {
      "node_type": "dataset",
      "name": "source",
      "dataset_id": "<source_dataset_id>",
      "load_existing_data": true,
      "position": [1, 1]
    }
  }
}
```

## Appendix B: opening prompt

Paste this to start a gated session:

```text
Using the doe-mcp MCP server, help me build a RAG setup on DOE from my PDFs.

Phase 0 — planning only. Read-only tools are fine; do not create, update, delete, install or
deploy anything until I reply "approved". In this phase:
  1. Tell me which parts of this the server supports directly and which it does not.
  2. Call project_list, then for the project I pick show datasets, existing pipelines, and the
     models already installed (app_list, model_list).
  3. Propose a plan using the direct RAG tools (rag_pipeline_create, rag_add_pdf_filter_node,
     rag_add_pdf_to_text_node, rag_add_text_to_chunks_node, rag_add_embedding_node,
     rag_add_output_node). If those don't fit, propose marketplace templates or hand-built
     pipelines as alternatives.
  4. Name every object you would create, the output dataset the text items, chunk items and
     vectors would land in, the chunk configuration you would use, and which already-deployed
     model you would embed with (by id) — or say plainly that no deployed model is available.

Then stop and wait. I may approve only part of the plan; honour that scope exactly.
After approval, work one phase at a time and show me each pipeline graph before running it.
Tell me plainly whenever something I ask for is not supported instead of substituting something else.
```
