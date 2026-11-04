# Installed-plugin validation plan

Plan and results record for the ten installed-plugin validation case families. Offline tests in this repository
establish local behavior with mocks; they do not establish backend behavior.
Each case below needs a run against a scoped, disposable environment before its
status moves past `not run`.

Rules for every run:

- Fresh chat per case, fixed fixtures, agreed expected outcome written *before* the run.
- Start each run with `scripts/record_run_metadata.py` and keep its JSON with the results.
- Record run status (`completed`, `timeout`, `step_limit`, `error`) separately from quality
  (`pass`, `fail`, `not_graded`); `validate_case_result` in the same script enforces this.
- Review valid alternative outputs manually; a different tool path is not automatically a failure.
- Never record credentials, private hostnames, or local paths.

## Case families

| # | Case | Environment needed | Minimum acceptance evidence | Offline coverage today | Status |
|---|---|---|---|---|---|
| 1 | Analytics answers and access | DDAE endpoint, governed dataset with known answers, restricted identity | Known-answer query, empty result and restricted identity: correct facts, no invented result, no unauthorized rows or columns | Fixture evals (`evals/cases/ddae.json`) | not run: needs DDAE endpoint |
| 2 | Search evidence and limits | DDSE endpoint, known indexed documents | Known document, no match, unavailable inference dependency: attributable evidence and an accurate limitation | Fixture evals (`evals/cases/ddse.json`) | not run: needs DDSE endpoint |
| 3 | Generated-code boundaries | None (offline) plus one Dataloop run | Valid IDs work; quotes, newlines, and code-like input in dataset and model IDs cannot change executable structure | `python_string_literal` in `rag.py` | live pass 2026-11-04 |
| 4 | PII reporting | Dataloop sandbox with synthetic nested PII | Nested synthetic PII and unrelated sensitive metadata never appear in the default report | `test_pii.py` allowlist test | live pass 2026-11-04 |
| 5 | PII training exclusion | Dataloop sandbox, clean, flagged, and mixed-entity items | Actual subset membership inspected; reported exclusion matches it | Filter construction only | live pass 2026-11-04 |
| 6 | Project isolation | Two projects, a denied identity | Mismatching and denied project or resource identities rejected before downstream access or mutation | `context._verify_resource_project` tests | live pass 2026-11-04 (two projects; second identity not tested) |
| 7 | RAG approval and cleanup | Dataloop sandbox with an embedding model | Approve and deny; embedding failure; cleanup failure. No unapproved work. Item and vector disposition and errors explicit | Mocked in `test_rag.py` | not run: needs a deployed embedding model (billable) |
| 8 | Same-name and repeat input | Dataloop sandbox | Two same-name documents plus reprocessing: distinct outputs, no cross-source overwrite, correct stale-chunk cleanup | Naming logic tested with mocks | not run: needs code-node execution (billable) |
| 9 | Default installation and stop | Clean machine, default profile, alternate config | Required tools present; each promised workflow completes; a running pipeline stops and stays stopped | Tool catalog and skill references checked statically | live pass 2026-11-04 (dataset-node pipeline) |
| 10 | Resume, retry, unsupported asks | Dataloop sandbox | Existing state inspected, no duplicate writes, clear stop outside supported scope | None | live pass 2026-11-04 (resume and duplicate-node cases) |

## Cross-checks outside the ten cases

- Capture the effective `tools/list` from the DDAE and DDSE endpoints with
  `scripts/capture_remote_mcp_contract.py`, keep one capture per endpoint, and reconcile any
  difference between Analytics captures.
- Every advertised tool needs accurate `readOnlyHint`, `destructiveHint`, and `openWorldHint`;
  tools returning `structuredContent` need an `outputSchema`. The DOE `plugin-v1` profile is
  enforced by `test_profiles.py`; the remote engines are audited from their captures.
- Workflow value (completed outputs, accuracy, elapsed time, human rework, cost including retries)
  is measured per engine only after the cases above pass.

## Results log

One row per run. Link the run-record JSON and per-case result files.

| Date | Case | Plugin and version | Package SHA-256 | Run status | Quality | Notes |
|---|---|---|---|---|---|---|
| 2026-11-04 | 3, 4, 5, 6, 9, 10 | dell-data-orchestration-engine 0.5.0 + fix branch | branch, not released | completed | pass (31/31) | `evals/results/doe-live-20261104/results.json`; clean install from the local marketplace into an empty `CODEX_HOME` started the server with 43 tools |
