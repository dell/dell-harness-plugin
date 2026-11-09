# DOE live retest, OpenAI review round 2 (2026-11-09)

Self-hosted DOE instance, a disposable project created for the run and deleted afterwards. Code
from branch `fix/openai-review-round2` (DOE 0.7.0). Script:
`dell-data-orchestration-engine/mcp-server/scripts/live_review_round2.py`. Results: `results.json`.

| Reviewer request | Result |
|---|---|
| Check item ownership before reads and writes | Item from dataset B with dataset A selected: `item_get`, `item_update`, `pii_set_findings`, `annotation_list` all rejected before any read or write; no item name leaked; item B metadata unchanged. Same-dataset control served. |
| Isolate each pipeline's output | Two RAG pipelines, one output dataset, the chunk code exactly as stored on the platform, run against live data. Pipeline A's 4 chunks were identical after pipeline B (different chunk size) processed the same source; separate per-pipeline folders; no shared items. |
| Declining a write in Codex stops it before anything changes | Codex CLI 0.159.0 with the installed DOE 0.7.0 bundle, normal prompt asking to record PII findings. Codex declined the approval (`user cancelled MCP tool call`, `decline-in-codex.jsonl`); the agent reported the write as not done; item metadata re-read from the platform was unchanged. |
| Setup check is read-only and does not report ready when blocked | Clean start: `launch=blocked`, zero files changed. |
| Clean start and recovery after interrupted setup | Preparation killed mid-install (process group): check reported `blocked ... after an interrupted preparation`. Next start removed the stale lock, rebuilt the venv, reported ready; check then `launch=ready`; the server started (43 tools) and `project_list` succeeded on the instance. |

The RAG chunk code was executed by the test process against the live dataset rather than by
deployed pipeline services, which would need a deployed embedding model and compute.
