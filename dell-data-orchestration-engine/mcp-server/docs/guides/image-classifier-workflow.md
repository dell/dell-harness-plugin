# Continuous-Learning Image Classifier with doe-mcp

A conversational walkthrough for standing up an image classifier that tags new images
automatically — person, animal, vehicle, building — and improves as you review and correct its
output. It is deliberately **human-gated**: Phase 0 is a planning conversation in which nothing is
executed, and the agent only starts calling mutating tools once you approve the plan. Read-only
discovery is allowed during planning so the plan is grounded in what actually exists.

Works with any MCP client — Devin, Claude Code or Desktop, and Codex have all been used against
this server. Nothing below is client-specific; see the README for per-client setup.

Requirements and recommendations use RFC-2119 language.

Each phase is a dialogue: **You** lines are what you type, **Agent** lines are the expected shape
of the reply, not verbatim output. Tool arguments live in [Appendix A](#appendix-a-tool-reference)
— the agent picks them, you approve the intent — and the session-wide rules in
[Guardrails](#guardrails-the-agent-must-respect). One rule not repeated there: the agent MUST show
you the exact code or template contents before anything runs on DOE. This workflow spends
money — `model_install`, `model_deploy`, `model_train` and every trigger firing consume platform
compute — and the agent MUST treat all of them as cost-incurring.

## Phase 0 — Agree the plan before anything runs

### 0.1 Set up and open the conversation

Configure `doe-mcp` in your MCP client per the README's **Deploy with MCP clients**, then start a
new session so the tools load. The server MUST receive `DATALOOP_API_KEY` and `DATALOOP_PATH`;
against a non-cloud deployment it MUST also receive `DATALOOP_GATE_URL`, plus
`DATALOOP_VERIFY_SSL=false` for a self-signed chain.

- **You:** I want to deploy an image classifier that tags new photos automatically with what's in
  them, and keeps improving as I correct it. Use the doe-mcp tools. Stay in planning mode: read-only
  tools are fine, but do not install, create, deploy, train, upload or trigger anything until I say
  "approved". Tell me what each step will cost me.
- **Agent:** Summarises the capability boundaries (§0.3), lists the read-only calls it wants to
  make, and asks which project to work in.

### 0.2 Ground the plan with read-only calls

During Phase 0 the agent SHOULD keep to the read-only tools in
[Appendix A](#appendix-a-tool-reference) — discovery only, nothing that writes or spends.

- **You:** Use project_list, then for the project I pick: show the datasets with item counts, the
  labels each candidate dataset's ontology defines, the models already installed, and any triggers
  already running. Then give me a plan with the cost of each step.

### 0.3 Capability boundaries the agent must state up front

| What you probably want | Supported? | Consequence for the plan |
| --- | --- | --- |
| Tag images with what's in them | Yes, as **whole-image classification** | "A person is present" — not *where* in the image |
| Bounding boxes around detected objects | **No** | Only classification annotations are supported. No boxes, points or polygons |
| Recognise *who* a person is | **No, out of scope** | Category recognition only. Face recognition and re-identification are a different capability, deliberately excluded |
| Reuse a model already installed | Yes | `app_list` + `model_list`; avoids paying for a fresh install |
| Tag new images automatically | Yes | An event trigger, scoped to one dataset — a project-wide trigger is refused |
| Retrain automatically on a schedule | **No** | Retraining is on demand only. Scheduled retraining is out of scope until cost bounds exist |
| Keep unreviewed tags out of training | Yes | A review marker in item metadata plus a training subset that requires it |
| Undo a bad auto-tagging run | Yes | `annotation_delete_by_model` removes only that model's annotations |
| See why a classifier tagged something | **No** | No per-prediction explainability |
| See a trigger's recent failures | **No** | `trigger_get` reports active/inactive, not what firings did — a known gap |

### 0.4 Approve, or send it back

- **Agent:** Plan: (1) confirm `<dataset>`'s ontology defines the labels we want; (2) install
  `<package>` and create model `<name>` *(cost: install + storage)*; (3) deploy it as a service
  *(cost: compute until stopped)*; (4) build the tagging pipeline and trigger it on new items in
  `<dataset>` *(cost: per item)*; (5) route tagged items to review and mark reviewed ones in
  metadata; (6) define the training subset to require the review marker; (7) retrain on demand once
  enough reviewed items accumulate *(cost: per training run)*. Nothing is created yet — reply
  "approved" to proceed, or tell me what to change.
- **You:** Approved for steps 1 to 3 only. Come back before you create the trigger.

## Phase 1 — Confirm the label vocabulary

- **You:** What labels can I actually tag with in this dataset? I want person, animal, vehicle and
  building.
- **Agent:** Calls `annotation_labels`, lists what the ontology defines and what is missing.
  Missing labels have to be added to the dataset's recipe first.

Annotations are ontology-backed, and this is not a formality: a live probe (2026-08-20) confirmed
the **platform itself accepts an out-of-ontology label** — it does not reject one.
`annotation_create` refuses client-side, and that refusal is the only thing between a typo and a
label nothing can resolve later. The agent SHOULD add that the labels are yours to choose, and that
"person" means "a person is present", never *which* person.

## Phase 2 — Get a classifier running

- **You:** Find me a classification model in the marketplace, and check what's already installed
  here before you install anything new.
- **Agent:** Calls `marketplace_search` and `marketplace_get_dpk` for each candidate's model
  components and labels, then `app_list` and `model_list` for models already in the project.
- **You:** Install that package, create a model bound to my dataset, and deploy it.
- **Agent:** Calls `model_install`, `model_create` with the dataset id and label subset, then
  `model_deploy` with `confirm: true`.

`model_deploy` requires `confirm: true` on **every** call, not only when service overrides are
supplied; it provisions billable compute that runs until the service is stopped. So does
`model_train`.

## Phase 3 — Tag new images automatically

- **You:** Now make it tag new photos as they arrive. Show me the graph, then create the trigger.
- **Agent:** Proposes a pipeline reaching the deployed model through a `function` node, shows it
  with `pipeline_get`, then calls `trigger_create` with `confirm: true` and a mandatory
  `dataset_id`.
- **You:** Upload a couple of test images, then tell me whether it tagged them.
- **Agent:** Calls `item_upload` — the response reports `uploaded` and `failed` **per file**,
  because uploads are not atomic — then `annotation_list` on the new items, showing each label and
  the model behind it.

The agent MUST say that a trigger **outlives this conversation**: it keeps firing, and costing
money, after the session ends. Predictions land as annotations on your items, alongside
human-authored ones, until you review them. `pipeline_execute` runs the pipeline once on demand,
billable like any firing; if a run wedges, `execution_list` shows what is in flight and
`execution_terminate` stops it.

## Phase 4 — Review, and correct

Auto-tags are predictions, not ground truth. This phase stops the classifier reinforcing its own
mistakes.

- **You:** Show me what it tagged, and let me mark the ones that are right.
- **Agent:** Calls `annotation_list` per item with label and model provenance, then marks approved
  items with `item_update`, writing a review marker into user metadata.
- **You:** That whole batch is wrong — the model was misconfigured.
- **Agent:** Calls `annotation_delete_by_model` **without** confirmation, which deletes nothing and
  reports the count: "That would remove 143 annotations written by model `<id>` in this dataset.
  Human-authored annotations are untouched. Confirm?"
- **You:** Confirmed.
- **Agent:** Re-calls with `confirm: true` and `expected_count` set to the number you agreed.

The marker lands at `metadata.user.<field>`, exactly the path Phase 5's subset filters on; written
anywhere else it is silently invisible, and that pairing is what makes the review gate bind.
`expected_count` matters because the Phase 3 trigger may still be writing: if the set grew, the
tool refuses rather than removing more than you authorised.

## Phase 5 — Train only on reviewed data

- **You:** Make sure training only uses the images I've reviewed, and keep PII-flagged images out.
- **Agent:** Calls `model_set_subset` for `train`, filtering on the Phase 4 review marker and
  adding `exclude_pii`, and reports how many items match. (See the PII guide for how items get
  flagged.)

The agent MUST relay a zero match: a subset matching nothing means training has nothing to draw on,
and hearing it now beats discovering it when the job fails.

## Phase 6 — Retrain

- **You:** Retrain it now, and tell me how it did.
- **Agent:** Confirms how many newly reviewed items exist, calls `model_train` with
  `confirm: true`, then `model_get` for the resulting status and version.

Training is billable per run. The agent MUST decline rather than run when there is nothing new to
train on, and MUST NOT start a second run for a model already training. There is no per-prediction
explainability, so comparing versions means comparing their outputs on items you have reviewed.

## Phase 7 — Iterate, and tear down

Every round repeats the same small gate: propose, show, confirm, apply, re-inspect.

- **You:** What automation is running in this project? Stop it and take the service down.
- **Agent:** Calls `trigger_list`, which returns **every** trigger in the project, not only the
  ones this session created, then `trigger_delete` with `confirm: true` — and says the deployed
  service keeps costing money until it is stopped separately.

`trigger_get` reports whether a trigger is active, not what its firings did — a classifier whose
every firing fails looks healthy. Cross-check with `pipeline_stats`, `execution_list` and
`annotation_list`.

## Guardrails the agent MUST respect

1. Do not call any mutating tool before the Phase 0 plan is approved, and honour partial approvals
   exactly.
2. Ask rather than guess which dataset, project or model is meant — including in read-only steps.
3. Treat `model_install`, `model_deploy`, `model_train` and `trigger_create` as cost-incurring;
   state the cost before asking for confirmation, and prefer a model already listed by
   `model_list`.
4. Never create a trigger without a `dataset_id`; a project-wide trigger has unbounded cost.
5. Never claim a label is taggable until `annotation_labels` shows it in the dataset's ontology,
   and never substitute a similar label for a missing one.
6. Never present a bulk annotation delete without first reporting the count, and pass
   `expected_count` when confirming so a growing set cannot silently widen the deletion.
7. Never report an upload batch as successful without checking the per-file `failed` list.
8. Never treat unreviewed auto-tags as ground truth for training.
9. Never claim a trigger is healthy on the basis of `active` alone.
10. State unsupported requests plainly — boxes, identity recognition and scheduled retraining are
    all out of scope, and MUST be named as such rather than approximated.

## Appendix A: tool reference

Read-only, safe during Phase 0 planning:

| Tool | Purpose |
| --- | --- |
| `project_list`, `project_get` | Resolve `project_id`; there is no ambient active project |
| `dataset_list`, `dataset_get` | Discover datasets and item counts |
| `item_list`, `item_get` | Inspect items and their metadata |
| `annotation_labels` | What the dataset's ontology allows you to tag with |
| `annotation_list`, `annotation_get` | Review auto-tags and their model provenance |
| `marketplace_search`, `marketplace_get_dpk` | Find a classification package and its components |
| `app_list`, `model_list`, `model_get` | See what is already installed, before paying to install more |
| `pipeline_list`, `pipeline_get`, `pipeline_stats` | Inspect the tagging pipeline and its counters |
| `trigger_list`, `trigger_get` | Find standing automation, including someone else's |

Mutating, allowed only after approval:

| Tool | Gate |
| --- | --- |
| `model_install` | `confirm: true` when `custom_installation` is supplied; cost-incurring |
| `model_create` | Plan approval |
| `model_deploy` | `confirm: true` **always**; provisions billable compute |
| `model_train` | `confirm: true` **always**; billable per run |
| `model_set_subset` / `model_delete_subset` | Plan approval |
| `item_upload` | Plan approval; not atomic — check per-file results |
| `item_update` | Plan approval; cannot reach platform `system` metadata |
| `annotation_create` | Plan approval; classification only, label must be in the ontology |
| `annotation_delete` | `confirm: true` |
| `annotation_delete_by_model` | `confirm: true` plus `expected_count`; call once unconfirmed to see the count |
| `pipeline_node_add` / `pipeline_node_update` | `confirm_code: true` for code nodes |
| `pipeline_connect_nodes` | Plan approval |
| `pipeline_install` | `confirm: true`; keeps consuming compute until paused or deleted |
| `trigger_create` | `confirm: true` **and** a mandatory `dataset_id` |
| `trigger_delete`, `model_delete`, `app_uninstall` | `confirm: true` plus an explicit instruction |

Key argument shapes:

```json
{
  "tool": "annotation_create",
  "arguments": {
    "project_id": "<project_id>",
    "dataset_id": "<dataset_id>",
    "item_id": "<item_id>",
    "label": "person",
    "model_id": "<model_id>",
    "confidence": 0.92
  }
}
```

```json
{
  "tool": "trigger_create",
  "arguments": {
    "project_id": "<project_id>",
    "name": "classify-on-upload",
    "dataset_id": "<watched_dataset_id>",
    "pipeline_id": "<tagging_pipeline_id>",
    "actions": ["created"],
    "confirm": true
  }
}
```

```json
{
  "tool": "annotation_delete_by_model",
  "arguments": {
    "project_id": "<project_id>",
    "dataset_id": "<dataset_id>",
    "model_id": "<model_id>",
    "confirm": true,
    "expected_count": 143
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
    "metadata_field": "reviewed",
    "exclude_pii": {"entity_types": ["US_SSN"]}
  }
}
```

```json
{"tool": "model_train", "arguments": {"project_id": "<project_id>", "model_id": "<model_id>", "confirm": true}}
```

## Appendix B: opening prompt

Paste this to start a gated session:

```text
Using the doe-mcp MCP server, help me deploy an image classifier that tags new photos
automatically and improves as I review its output.

Phase 0 — planning only. Read-only tools are fine; do not install, create, deploy, train, upload
or trigger anything until I reply "approved". In this phase:
  1. Tell me which parts of this the server supports directly and which it does not — in
     particular whether I get bounding boxes, and whether retraining can be scheduled.
  2. Call project_list, then for the project I pick show datasets with item counts, the labels
     each candidate dataset's ontology defines, the models already installed, and any triggers
     already running.
  3. Search the marketplace for a classification package and show me its model components.
  4. Propose a numbered plan naming every object you would create, and state which steps cost
     money before I approve them.

Then stop and wait. I may approve only part of the plan; honour that scope exactly.

Ask me rather than guessing which dataset, project or model I mean — including for read-only
calls. Never tag with a label the ontology does not define. Before any bulk annotation delete,
show me the count and pass it back as expected_count when I confirm. Tell me plainly that a
trigger keeps firing and costing money after this conversation ends.
```
