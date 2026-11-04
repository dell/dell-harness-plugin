# PII Detection and Handling with doe-mcp

A conversational walkthrough for answering "does this dataset contain PII?" and acting on the answer:
run DOE's Presidio solution, record what it found on each item, route flagged items into a
quarantine dataset, and keep flagged data out of downstream work. The workflow is deliberately
**human-gated**: Phase 0 is a planning conversation in which nothing is executed, and the agent only
starts calling mutating tools once you approve the plan. Read-only discovery is allowed during
planning so the plan is grounded in what actually exists.

Works with any MCP client — Devin, Claude Code or Desktop, and Codex have all been used against
this server. Nothing below is client-specific; see the README for per-client setup.

Requirements and recommendations use RFC-2119 language. Each phase is a dialogue: **You** lines are
what you type, **Agent** lines are the expected shape of the reply, not verbatim output. Arguments
live in [Appendix A](#appendix-a-tool-reference); standing requirements are listed once, under
[Guardrails](#guardrails-the-agent-must-respect), and repeated in a phase only where the phase would
mislead without them.

## Phase 0 — Agree the plan before anything runs

### 0.1 Set up the session

Configure `doe-mcp` in your MCP client as the README describes under **Deploy with MCP clients**. The
server MUST receive `DATALOOP_API_KEY` and `DATALOOP_PATH`; against anything other than the DOE
cloud it MUST also receive `DATALOOP_GATE_URL`, plus `DATALOOP_VERIFY_SSL=false` for a self-signed
chain. Start a new session so the MCP tools load.

### 0.2 Open the conversation

- **You:** I want to find out whether one of my datasets contains PII, and then deal with it, using
  the doe-mcp tools. Stay in planning mode: read-only tools are fine, but do not create, upload,
  update, delete, install, deploy or run anything until I say "approved". Start by telling me which
  parts of this the server supports directly and which it doesn't.
- **Agent:** Summarises the capability boundaries (§0.4), lists the read-only calls it wants to make,
  and asks which project to work in.

### 0.3 Ground the plan with read-only calls

The agent SHOULD use only these during Phase 0: `project_list`, `project_get`, `dataset_list`,
`dataset_get`, `item_list`, `item_get`, `marketplace_search`, `marketplace_get_dpk`, `pipeline_list`,
`pipeline_get`, `pipeline_stats`, `pii_report`, `pii_filter`, `pii_writeback_code`.

- **You:** Use project_list, then for the project I pick, show me the datasets with item counts, any
  pipelines that already exist, and search the marketplace for a PII or Presidio solution with
  templates_only=true. Then give me a plan.

### 0.4 Capability boundaries the agent must state up front

| What you probably want | Supported? | Consequence for the plan |
| --- | --- | --- |
| Ask "does this dataset have PII?" | Yes, **if a scan has already run** | `pii_report` reads a marker on items; it does not itself inspect file contents |
| Have doe-mcp detect the PII | **No** | Detection is the Presidio solution's job. doe-mcp finds, installs, runs and wires it — it never reads a document itself |
| Know how Presidio marks its findings | **Not verified** | The marker convention is supplied by you, not assumed. Phase 1 exists to establish it |
| Move flagged items to another dataset | **Not a move** | There is no cross-dataset move. A pipeline dataset node **copies** matching items; originals stay unless separately deleted (Phase 5) |
| Skip flagged items downstream | Yes | `pii_filter` returns a reusable query; `model_set_subset` applies it to training |
| Store what the PII actually said | **No** | Only entity *type* names are recorded. The values are the PII, and metadata is queryable and exportable |
| See per-item scan progress | **No** | Only `pipeline_stats` counters and dataset counts |
| See a trigger's recent failures | **No** | `trigger_get` reports whether a trigger is active, not what its firings did — a known gap |

### 0.5 Approve, or send it back

- **Agent:** Plan: (1) establish the marker convention on a dataset known to contain PII; (2) install
  the Presidio solution and run it over `<dataset>`; (3) add the writeback code node; (4) verify on
  one processed item, then report the dataset's PII status; (5) create `<dataset>-pii-quarantine` and
  route flagged items to it; (6) produce the exclusion filter and apply it to `<model>`'s training
  subset. Nothing is created yet. Reply "approved", or tell me what to change.
- **You:** Approved for steps 1 to 4 only. Stop before you create any dataset or route anything.
  (Partial approval like that is the recommended default.)

## Phase 1 — Establish how PII is marked

The worst failure here is silent: if the marker convention is wrong, **every dataset reports clean**.

- **You:** I have a dataset I know contains PII. Work out how findings are recorded on its items.
- **Agent:** Asks which dataset, then calls `item_list` and `item_get` on processed items and shows
  their `metadata` and annotations verbatim.

Three conventions are supported, and the agent MUST get you to choose one:

| Marker shape | Use when | Query strength |
| --- | --- | --- |
| `entity_types: ["US_SSN", ...]` | The writeback in Phase 3 is in use | Strongest — exact, negates cleanly |
| `metadata_field: "..."` | Another scanner writes its own flag | Strong — negates cleanly |
| `annotation_label: "..."` | Findings arrive as annotations | **Weakest** — see below |

For the annotation-label shape the agent MUST state the caveat: excluding by label uses a negated
join, which DQL evaluates per annotation, so an item carrying both a PII and a non-PII annotation can
still pass an "exclude PII" filter.

- **You:** Use entity_types, and treat US_SSN, CREDIT_CARD and EMAIL_ADDRESS as PII here.

### Known blocker in the aidp-dev deployment (2026-08-21)

`pii-anonymizer-pipeline` cannot be instantiated there: the platform's validator rejects it with
`('400', 'Invalid roles')` before anything is created. A plain `pipeline_create` succeeds for the same
account, so it is the template, not the tooling or the caller's role. Until a DOE administrator
resolves it, Phases 2 to 5 cannot run there — the agent MUST say so rather than retrying, and MUST NOT
present the resulting empty `pii_report` as evidence a dataset is clean.

## Phase 2 — Install and run the Presidio solution

- **You:** Find the Presidio PII solution and show me what it provides before installing anything.
- **Agent:** Calls `marketplace_search` with `templates_only: true`, then `marketplace_get_dpk` on the
  candidates, listing each template's `name`, `description`, `node_count`, `node_names`, `node_types`.
- **You:** Create the pipeline from that template, then stop.
- **Agent:** Calls `pipeline_create_from_template` with `confirm: true`, reporting that the template
  validated against the platform first and that the pipeline was created but **not** started.

The agent MUST NOT claim a solution exists until a search returns it — availability depends on what
your organisation publishes. If `scan_truncated` is `true`, matches may exist beyond the scan cap and
the agent SHOULD say so.

## Phase 3 — Record findings on each item

The scan alone leaves nothing queryable on the source items. A code node downstream of the anonymizer
writes each item's verdict into its own metadata.

- **You:** Show me the writeback code before you add it.
- **Agent:** Calls `pii_writeback_code` and shows the source, the field it reads
  (`metadata.user.presidio_results`), and what happens if that field name is wrong.

**The input field is an assumption.** If the anonymizer hands findings on differently, the node writes
an empty map for every item — which reads as "scanned, nothing found". That is a false negative that
looks exactly like success.

The node writes a flat map of detected entity type to `true`:

```json
{"US_SSN": true, "EMAIL_ADDRESS": true}
```

No counts, no confidence scores, no matched values. Undetected types are absent rather than `false`.
An item scanned and found clean carries an **empty** map — that empty map is the only record a scan
ran at all, and the platform does persist it (verified live, 2026-08-20).

- **You:** Code looks right. Add it downstream of the anonymizer and show me the graph.
- **Agent:** Adds the node with `pipeline_node_add` and `confirm_code: true`, connects it with
  `pipeline_connect_nodes`, then calls `pipeline_get` with `include_graph: true`.
- **You:** Run it over the small dataset first.
- **Agent:** Calls `pipeline_install`, then reports `pipeline_stats` counters.

### 3.1 Verify before trusting

- **You:** Pick one item the pipeline has processed and show me its metadata.
- **Agent:** Calls `item_get` and shows `metadata.user.pii` verbatim. Findings live under the `user`
  namespace — `metadata.user.pii`, not `metadata.pii` — the path every PII query and filter targets.

If the map is empty on an item you know contains PII, the `reads_from` assumption is wrong: the node
source needs adjusting, not the marker.

## Phase 4 — Answer the question

- **You:** So — does this dataset have any PII?
- **Agent:** Calls `pii_report` and reports the flagged count out of the dataset total, a sample, and
  the marker it used.

Guardrails 3 and 4 bite here: a **zero** means no item carries the marker, not that the dataset is
clean (`pii_report` returns that caveat itself), and a missing platform total makes the count one
page rather than the whole dataset. A third case the agent MUST relay: if anything in the chain
could not **read** an item — an unparseable PDF, an image-only scan, a text layer that decodes to
nonsense — that item has no verdict, clean or otherwise. A detector that cannot see a document
reports no PII in it, and that looks exactly like a genuine all-clear. The agent MUST NOT let
unreadable items contribute to a clean result, and SHOULD confirm the scanner read a sample document
before anyone relies on a zero.

## Phase 5 — Route flagged items to a quarantine dataset

- **You:** Move the PII items into a separate dataset.
- **Agent:** States plainly that this is a **copy, not a move** — the pipeline dataset node clones
  matching items to the target and the originals remain in the source — then proposes: create
  `<source>-pii-quarantine` with `dataset_create`, add a dataset node targeting it, and connect it
  downstream of the filter.
- **You:** Understood, do that. Leave the originals for now.

## Phase 6 — Skip flagged items downstream

Two ways, and they compose.

- **You:** Give me a filter that skips the PII items.
- **Agent:** Calls `pii_filter` with `mode: "exclude"` and returns the query, which you can hand to a
  pipeline dataset node or reuse elsewhere.
- **You:** Now make sure my classifier never trains on them.
- **Agent:** Calls `model_set_subset` with `exclude_pii` set to the same marker, and reports how many
  items the subset matches.

The agent SHOULD relay that match count: a subset matching **no** items means training would have
nothing to draw on, and that is better heard now than when the training job fails.

## Phase 7 — Keep it running, and tear it down

- **You:** Make this happen automatically for new files.
- **Agent:** Proposes `trigger_create` scoped to the source dataset. A `dataset_id` is mandatory — a
  project-wide item trigger is refused, because scoping is the only cost limit the server can impose.
  Requires `confirm: true`. A trigger is standing automation: it keeps firing, and billing, until
  someone deletes it.
- **You:** What automation is running in this project?
- **Agent:** Calls `trigger_list`, which returns **every** trigger in the project, not only ones this
  session created — so automation left behind earlier is still findable.
- **You:** Tear it down.
- **Agent:** Calls `trigger_delete` with `confirm: true`, then reports what remains.

The agent MUST state the limit: `trigger_get` says whether a trigger is active, not what its firings
did. A trigger whose every execution fails looks identical to a healthy one with nothing to do.
Cross-check with `pipeline_stats` and dataset counts.

## Guardrails the agent MUST respect

1. Do not call any mutating tool before the Phase 0 plan is approved, and honour partial approvals
   exactly.
2. Ask rather than guess which dataset, project or model is meant — including in read-only steps. A
   guessed dataset in a read step becomes the assumed dataset in the mutating step that follows.
3. Never present a zero `pii_report` result as "this dataset is clean". Relay the caveat.
4. Never use a count the tool marked inexact as the basis for a bulk action.
5. Verify the writeback on one processed item before trusting any dataset-level answer.
6. Never describe the quarantine routing as a "move". It copies; originals remain, and deleting them
   is a separate step the operator must ask for in those words.
7. Never write matched PII values into metadata. Only entity type names are recorded, and no tool here
   accepts the values.
8. Never add or replace code-node source without showing the exact code first; `confirm_code: true`
   exists because that code runs on DOE runners.
9. Treat `pipeline_create_from_template`, `model_install`, `model_deploy`, `model_train` and
   `trigger_create` as cost-incurring, and confirm before using them.
10. State unsupported requests plainly rather than silently substituting an alternative.

## Appendix A: tool reference

Read-only, safe during Phase 0 planning:

| Tool | Purpose |
| --- | --- |
| `project_list`, `project_get` | Resolve `project_id`; there is no ambient active project |
| `dataset_list`, `dataset_get` | Discover datasets and item counts |
| `item_list`, `item_get` | Inspect items and their metadata — how the marker is established |
| `marketplace_search`, `marketplace_get_dpk` | Find the Presidio solution; inspect its templates |
| `pipeline_list`, `pipeline_get`, `pipeline_stats` | Inspect pipelines and counters |
| `pii_report` | Flagged count, total, sample, and the marker used |
| `pii_filter` | Build a reusable include/exclude query |
| `pii_writeback_code` | The code-node source, for review before deployment |
| `trigger_list`, `trigger_get` | Find standing automation, including someone else's |

Mutating, allowed only after approval:

| Tool | Gate |
| --- | --- |
| `dataset_create` | Plan approval |
| `pipeline_create_from_template` | `confirm: true`; validates the template first |
| `pipeline_node_add` / `pipeline_node_update` | `confirm_code: true` for code nodes |
| `pipeline_connect_nodes` | Plan approval |
| `pipeline_install` | `confirm: true`; keeps consuming compute until paused or deleted |
| `pii_set_findings` | Plan approval; writes only entity type names |
| `item_update` | Plan approval; cannot reach platform `system` metadata |
| `model_set_subset` | Plan approval |
| `trigger_create` | `confirm: true` **and** a mandatory `dataset_id` |
| `trigger_delete`, `pipeline_delete`, `dataset_delete` | `confirm: true` plus an explicit instruction |

Key argument shapes:

```json
{"tool": "marketplace_search", "arguments": {"name": "presidio", "templates_only": true, "page_size": 50}}
```

```json
{
  "tool": "pii_report",
  "arguments": {
    "project_id": "<project_id>",
    "dataset_id": "<dataset_id>",
    "marker": {"entity_types": ["US_SSN", "CREDIT_CARD", "EMAIL_ADDRESS"]}
  }
}
```

```json
{
  "tool": "pii_filter",
  "arguments": {
    "project_id": "<project_id>",
    "dataset_id": "<dataset_id>",
    "marker": {"entity_types": ["US_SSN"]},
    "mode": "exclude"
  }
}
```

```json
{
  "tool": "model_set_subset",
  "arguments": {
    "project_id": "<project_id>",
    "model_id": "<model_id>",
    "subset": "train",
    "exclude_pii": {"entity_types": ["US_SSN", "CREDIT_CARD"]}
  }
}
```

```json
{
  "tool": "trigger_create",
  "arguments": {
    "project_id": "<project_id>",
    "name": "pii-scan-on-upload",
    "dataset_id": "<source_dataset_id>",
    "pipeline_id": "<pii_pipeline_id>",
    "actions": ["created"],
    "confirm": true
  }
}
```

## Appendix B: opening prompt

Paste this to start a gated session:

```text
Using the doe-mcp MCP server, help me find out whether a dataset contains PII and then deal with it.

Phase 0 — planning only. Read-only tools are fine; do not create, upload, update, delete, install,
deploy or run anything until I reply "approved". In this phase:
  1. Tell me which parts of this the server supports directly and which it does not — in
     particular, whether it detects PII itself and whether it can move items between datasets.
  2. Call project_list, then for the project I pick show datasets with item counts and any
     existing pipelines.
  3. Search the marketplace with templates_only=true for a PII / Presidio solution and show me
     what each package provides.
  4. Propose a numbered plan naming every object you would create and the exact writeback code
     you would deploy.

Then stop and wait. I may approve only part of the plan; honour that scope exactly.

Ask me rather than guessing which dataset, project or model I mean — including for read-only
calls. Before any dataset-level answer, verify the marker convention against one item I tell you
contains PII. Never report a zero result as "clean" without saying why it might not be, and never
describe copying items to a quarantine dataset as "moving" them.
```
