# DDSE Codex-first initial review evidence

Status: the local, synthetic Codex evidence matrix is complete. Attempt 7
passed all nine selected cases: six positive and three negative. ChatGPT
transcripts are explicitly deferred for the initial review and are not claimed
as completed.

## Scope

- Fixture: `ddse-synthetic-operations-v1`
- Data classification: synthetic, non-customer
- Surface: Codex CLI with a local, read-only fixture MCP server
- Selected surface: 10 deterministic tools
- Conditional surface: 15 tools excluded from the selected cases
- Evaluated build: `0.1.1+codex.20260910222649`
- Clean review release: `0.2.0` (version-only normalization; no functional
  change after the final evidence run)

Three Agent Builder tools remain conditional on a validated chat connector.
The other conditional tools require individually validated installed content,
embeddings, ML jobs, solution data, licensing, privileges, or fixtures.

## Final matrix

| Case | Class | Result | Evidence |
|---|---|---|---|
| `ddse-core-data-esql` | Positive | Pass | [result](attempt-7/ddse-core-data-esql/result.json), [transcript](attempt-7/ddse-core-data-esql/transcript.jsonl), [audit](attempt-7/ddse-core-data-esql/audit.jsonl) |
| `ddse-core-data-document` | Positive | Pass | [result](attempt-7/ddse-core-data-document/result.json), [transcript](attempt-7/ddse-core-data-document/transcript.jsonl), [audit](attempt-7/ddse-core-data-document/audit.jsonl) |
| `ddse-service-dependencies` | Positive | Pass | [result](attempt-7/ddse-service-dependencies/result.json), [transcript](attempt-7/ddse-service-dependencies/transcript.jsonl), [audit](attempt-7/ddse-service-dependencies/audit.jsonl) |
| `ddse-service-trace-logs` | Positive | Pass | [result](attempt-7/ddse-service-trace-logs/result.json), [transcript](attempt-7/ddse-service-trace-logs/transcript.jsonl), [audit](attempt-7/ddse-service-trace-logs/audit.jsonl) |
| `ddse-log-rate-categories` | Positive | Pass | [result](attempt-7/ddse-log-rate-categories/result.json), [transcript](attempt-7/ddse-log-rate-categories/transcript.jsonl), [audit](attempt-7/ddse-log-rate-categories/audit.jsonl) |
| `ddse-log-analysis-misspelled` | Positive | Pass | [result](attempt-7/ddse-log-analysis-misspelled/result.json), [transcript](attempt-7/ddse-log-analysis-misspelled/transcript.jsonl), [audit](attempt-7/ddse-log-analysis-misspelled/audit.jsonl) |
| `ddse-core-data-restricted` | Negative | Pass | [result](attempt-7/ddse-core-data-restricted/result.json), [transcript](attempt-7/ddse-core-data-restricted/transcript.jsonl), [audit](attempt-7/ddse-core-data-restricted/audit.jsonl) |
| `ddse-cases-write` | Negative | Pass | [result](attempt-7/ddse-cases-write/result.json), [transcript](attempt-7/ddse-cases-write/transcript.jsonl), [audit](attempt-7/ddse-cases-write/audit.jsonl) |
| `ddse-infrastructure-ambiguous` | Negative | Pass | [result](attempt-7/ddse-infrastructure-ambiguous/result.json), [transcript](attempt-7/ddse-infrastructure-ambiguous/transcript.jsonl), [audit](attempt-7/ddse-infrastructure-ambiguous/audit.jsonl) |

The machine-readable aggregate is [attempt-7/summary.json](attempt-7/summary.json).
Every per-case directory also includes the final answer and a sanitized audit
event derived from the genuine Codex JSONL transcript. The result records this
as `evaluation_harness_from_codex_jsonl`; it is not represented as a backend
Elastic audit log. Files are mode `0600` on POSIX.

## Iteration record

Earlier attempts are retained rather than overwritten: attempt 1 passed 4/9,
attempt 2 passed 3/9, attempt 3 passed 8/9, attempt 4 passed 7/9, and attempt 5
passed 9/9 behavioral checks but lacked resolvable audit files. They exposed
tool-path broadening, an insufficient ambiguity clarification, brittle wording
checks, and the audit-artifact gap. Attempt 6 passed 9/9 with resolvable audit
records. Attempt 7 repeated the 9/9 result from the final installed plugin and
adds portable links plus `0600` permissions for every generated evidence file;
it is the review candidate.

## Remaining external blockers

The live Elastic-owned MCP contract still lacks output schemas and the required
safety annotations. The final hosted endpoint, TLS path, least-privilege review
API key, Kibana-space boundary, and externally reachable review runtime also
need to be verified before external submission. These are not replaced by the
local fixture evidence.

## August 2026 guide alignment

The plugin-owned package aligns with the guide's recommendations for a focused
v1 surface, workflow-specific skills, explicit read-only boundaries, bounded
queries, stable synthetic fixtures, realistic positive and negative evals,
failure-driven iteration, complete listing metadata, sample prompts, and brand
assets.

It is not yet an unqualified OpenAI submission candidate because the endpoint
that reviewers scan is authoritative. Before submission:

1. Add output schemas and accurate safety annotations to every submitted live
   MCP tool. Skill instructions do not replace server metadata.
2. Ensure the reviewed endpoint exposes only the intended v1 tools, or validate
   every advertised tool and dependency. Workflow-level filtering does not
   remove the 15 conditional tools from `tools/list`.
3. Provide a stable, publicly reachable HTTPS MCP endpoint and production-like
   synthetic review account or credentials that work without private-network
   access, MFA, SMS, or email confirmation.
4. Re-run the final contract scan and hero cases against that endpoint. Add
   ChatGPT coverage before the unified public submission; the current deferral
   applies only to this initial Codex-first review package.
5. Confirm publisher/domain verification and public privacy, terms, website,
   and support information in the submission portal.
