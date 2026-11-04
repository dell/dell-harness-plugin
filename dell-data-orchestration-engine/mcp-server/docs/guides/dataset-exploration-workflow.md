# Exploring a Dataset with doe-mcp

A conversational walkthrough for the question "what is actually in this dataset?" — list the
datasets in a project, pick one, learn what kinds of files it holds, and get an executive summary
of its contents.

Most of this costs nothing and changes nothing. The last phase, where a model looks at the files
themselves, is billable **and mutates the dataset**, so it is gated separately and is genuinely
optional — read Phase 4 before you agree to it.

Works with any MCP client — Devin, Claude Code or Desktop, and Codex have all been used against
this server. Nothing below is client-specific; see the README for per-client setup.

Requirements and recommendations use RFC-2119 language.

Lines marked **You** are what you type; lines marked **Agent** are the expected shape of the
reply, not verbatim output. Tool arguments are in [Appendix A](#appendix-a-tool-reference), and
the agent's obligations are listed in full under [Guardrails](#guardrails-the-agent-must-respect)
— the phases repeat only what a reader would otherwise misread.

## Phase 0 — What this can and cannot tell you

The agent SHOULD state these limits first: they bound what an "executive summary" can honestly
contain.

| What you probably want | Supported? | How |
| --- | --- | --- |
| List the datasets in a project | Yes | `dataset_list` |
| What kinds of files are in one | Yes, free | `dataset_profile` — mimetypes, folders, annotation coverage |
| Exact counts on a large dataset | **Bounded** | No aggregation API exists, so counts come from a capped client-side scan. Above the cap they are proportions of a sample |
| A written executive summary | Yes — **the agent writes it** | The server returns facts; the agent narrates them. `doe-mcp` hosts no language model |
| Know what is *inside* the images | Only with a model | Filenames and labels are all the profile can see. Looking at pixels means running a model (Phase 4) |
| Run CLIP to summarise contents | **Not as stated** | CLIP produces embeddings and image↔text similarity. It does **not** write prose. It can score images against labels *you* supply; a captioning model generates text |
| Use the platform's Clustering app | **Partly** | Its inputs, yes — `dataset_embeddings_status` and `dataset_embed` drive the same feature sets it consumes. Its output, no: it is a TensorBoard projection rendered for a person, with no API returning cluster membership |
| Group items by similarity and describe the themes | Yes | `dataset_cluster` groups the vectors here and returns sizes and examples. It names nothing — see Phase 5 |
| Explore without changing anything | Phases 1-3 yes, Phase 4 no | Model predictions are written back as annotations |

## Phase 1 — List the datasets

- **You:** Please list all the datasets in this project.
- **Agent:** Calls `project_list` if no project is named yet, asks which one, then `dataset_list`,
  presenting a numbered table of name, id and `items_count`, paging while `has_more` is true.
- **You:** Use number 2.
- **Agent:** Confirms the choice by name *and* id.

## Phase 2 — What kind of files does it have?

- **You:** Tell me more about this dataset. What kind of files does it have?
- **Agent:** Calls `dataset_profile` and reports the file types, folder structure, annotation
  coverage and a few example filenames.

A profile is free, read-only and instant, and answers the file-type question completely. The one
field to read carefully is `scan_truncated`: the platform offers no aggregation, so the profile
counts by paging up to a cap, and above that cap the numbers describe a sample.

- **You:** So it's 400 PDFs and 100 images?
- **Agent:** "No — I scanned the first 500 of 12,000 items. In that sample it was 80% PDF and 20%
  image. The proportions are indicative, the absolute numbers are not. I can raise the cap." The
  tool returns a `caveat` string for exactly this case, and the agent SHOULD relay it.

## Phase 3 — An executive summary from what we already know

- **You:** Can you give me an executive summary of the contents?
- **Agent:** Writes prose from the profile — the mix of file types, how the folders are organised,
  what the filenames suggest, how much is annotated and against which ontology labels
  (`annotation_labels`), and what is conspicuously absent.

The summary is the **agent's** work, not the server's: `doe-mcp` returns structured facts and
hosts no language model. "The folder is `/invoices-2025` and every file is a PDF" is an
observation; "this is a set of 2025 invoices" is an inference from the filename and MUST be
labelled as one, because nothing has read the documents. For many datasets this is enough, and it
is where the workflow should stop.

## Phase 4 — Look inside the files (optional, billable, mutating)

Only worth doing when names, folders and labels are uninformative — a dataset of
`IMG_4821.jpg`-style filenames, for instance.

### 4.1 Choose from what is already installed

- **You:** The filenames tell me nothing. Can you work out what's actually in the images?
- **Agent:** Calls `model_list` and `app_list` and presents the models **already installed in this
  project** with their `status`, since an undeployed model produces no predictions: "Installed
  here: `clip-model-pretrained` (not deployed), `blip-image-captioning-large` (deployed),
  `yolov8` (deployed). Which should I use? CLIP scores images against labels you supply rather
  than describing them, so for a description of the contents the captioning model fits better."

### 4.2 Understand the price before agreeing

- **You:** Use the captioning model.
- **Agent:** Calls `model_predict` **without** confirmation first, which runs nothing and reports
  which model would run over how many items.

Two consequences the agent MUST state before you confirm. **Inference is billable** per item, so
prefer a sample over a whole dataset. And **predictions are written back as annotations**: a
dataset you only wanted to *explore* comes back changed. `annotation_delete_by_model` removes
exactly that model's contribution.

- **You:** Run it over 20 items first, not the whole thing.
- **Agent:** Calls `model_predict` with those `item_ids` and `confirm: true`, then polls
  `annotation_list` — prediction is asynchronous and annotations appear as it progresses.

### 4.3 Summarise what the model found

- **You:** Now summarise what's in there.
- **Agent:** Calls `annotation_list` across the sampled items, aggregates the labels, and reports
  *"in a 20-item sample the model tagged mostly X and Y"*, never a claim about the whole dataset.
- **You:** Actually, undo that — I didn't want the dataset annotated.
- **Agent:** Calls `annotation_delete_by_model` unconfirmed to report the count, then re-calls with
  `confirm: true` and `expected_count`.

## Phase 5 — Group by similarity when there is nothing to read (optional)

Useful when a dataset is images with meaningless filenames and no annotations: clustering shows
its shape before anything has been labelled.

- **You:** Can you group these by similarity so I can see what's in here?
- **Agent:** Calls `dataset_embeddings_status` first, which is free. If there are none: "No
  embeddings here. I can extract them with a model installed in this project, but that runs it
  over all 1,240 items on a high-memory machine and is billable — the same operation as the
  console's *Add CLIP Embeddings* button. Shall I?"

The item ceiling exists so a 500,000-item extraction cannot happen in one reflexive step; the
agent MUST NOT raise it to force a large dataset through. Embedding new arrivals automatically is
a **second** decision: `attach_trigger` leaves automation running that costs money per arrival
long after this conversation, and that standing cost MUST be named separately from the one-off.

- **You:** Extract them, no trigger.
- **Agent:** Runs it, then polls `dataset_embeddings_status` until the feature set's `vector_count`
  stops climbing; extraction is asynchronous, and `size` is the vector dimension, not a count, so
  it never moves. Only once the count has settled does it compare it with the item count:
  `dataset_embed`'s own `vectors_written` is a snapshot taken at submission and will normally be
  far short. A *settled* count below the item count usually means the model's `input_type` does
  not cover every item (e.g. an image-only model over a dataset that also holds PDFs) — DOE
  neither raises nor warns in that case, so the count is the only signal, and `execution_list` on
  the model's service shows the per-item outcomes.
- **You:** Now cluster them.
- **Agent:** Calls `dataset_cluster` and reports group sizes, shares and example items: "Four
  groups: 41%, 28%, 19%, 12%. I have not looked at any of them yet — shall I fetch the examples
  from the largest?"

**k-means returns groups, not names.** Cluster 0 is not a category, only a bag of vectors sitting
near each other, and `item_get` on its examples is the only honest route to a description. Two
further limits: if `scan_truncated` is true the shares describe the vectors read, not the dataset;
and CLIP vectors and text vectors answer different questions, so a dataset with several feature
sets needs you to choose.

## Guardrails the agent MUST respect

1. Ask which project and which dataset; never guess, even for read-only calls.
2. Page through any list reporting `has_more: true`.
3. Never quote a truncated scan's counts as dataset totals; relay the `caveat`.
4. Separate observation from inference in the summary, and say which is which.
5. Never claim to know what is inside a file that nothing has read.
6. Offer already-installed models before proposing an install.
7. State that CLIP does not write descriptions when a caller asks it to summarise contents.
8. Call `model_predict` unconfirmed first, and report cost and item count before asking.
9. Say plainly that predictions mutate the dataset, and name the undo.
10. Never present an asynchronous prediction as finished when the call returns.
11. Check `dataset_embeddings_status` before offering to cluster; never extract without
    confirmation, and never raise the item ceiling to force a large dataset through.
12. Name the standing cost of `attach_trigger` separately from the one-off extraction cost.
13. Never name a cluster from its number or size; fetch its example items and describe what is
    there, or say the group is uninspected.
14. Ask which feature set to cluster when a dataset has more than one.

## Appendix A: tool reference

Read-only and free:

| Tool | Purpose |
| --- | --- |
| `project_list`, `project_get` | Resolve the project |
| `dataset_list`, `dataset_get` | List datasets with item counts |
| `dataset_profile` | File types, folders, annotation coverage, sample filenames, scan honesty |
| `item_list`, `item_get` | Inspect individual items |
| `annotation_labels` | What the dataset's ontology defines |
| `annotation_list` | What a model or annotator tagged an item with |
| `model_list`, `app_list` | What is already installed, and whether it is deployed |
| `dataset_embeddings_status` | Whether a dataset has feature vectors, and so whether it can be clustered |
| `dataset_cluster` | Groups items by embedding similarity; returns sizes and examples, never names |

Mutating or billable:

| Tool | Gate |
| --- | --- |
| `model_predict` | `confirm: true`; billable per item, writes annotations onto the items |
| `annotation_delete_by_model` | `confirm: true` plus `expected_count`; the undo for the above |
| `model_install`, `model_deploy` | `confirm: true`; only if nothing installed will do |
| `dataset_embed` | `confirm: true`; billable per item, refuses above an item ceiling, and `attach_trigger` creates standing automation |

Key argument shapes:

```json
{"tool": "dataset_profile", "arguments": {"project_id": "<project_id>", "dataset_id": "<dataset_id>", "max_scan": 500}}
```

```json
{"tool": "dataset_embed", "arguments": {"project_id": "<project_id>", "dataset_id": "<dataset_id>", "model_id": "<clip_model_id>", "confirm": true}}
```

```json
{"tool": "dataset_cluster", "arguments": {"project_id": "<project_id>", "dataset_id": "<dataset_id>", "clusters": 4}}
```

```json
{
  "tool": "model_predict",
  "arguments": {
    "project_id": "<project_id>",
    "model_id": "<installed_model_id>",
    "item_ids": ["<item_id>", "<item_id>"],
    "confirm": true
  }
}
```

## Appendix B: opening prompt

```text
Using the doe-mcp MCP server, help me understand what is in one of my datasets.

Start read-only. Ask me which project rather than guessing, then list the datasets with their
item counts and let me pick one.

Once I pick one, profile it and tell me what kinds of files it holds, then give me an executive
summary. Be explicit about which parts you observed and which you inferred from filenames — and
if the profile only scanned a sample, say so rather than quoting the sample as a total.

If the filenames are uninformative, you may offer to group the items by similarity — check
whether embeddings already exist before proposing to extract any, tell me what extraction costs,
and when you report clusters, do not name them until you have looked at their example items.

Do not run any model over the data unless I ask. If I do ask, show me the models already
installed in this project and let me choose, tell me what it will cost and that predictions get
written back onto the items as annotations, and run over a small sample before anything larger.
```
