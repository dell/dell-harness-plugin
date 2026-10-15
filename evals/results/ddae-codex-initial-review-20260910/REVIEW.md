# DDAE Codex-first initial review evidence

Status: the plugin-owned package and local synthetic Codex evidence matrix are
ready for initial OpenAI plugin review. Attempt 6 passed all 12 selected
cases: six positive and six negative. ChatGPT transcripts are explicitly
deferred for this initial review and are not claimed as completed.

## Scope

- Fixture: `ddae-synthetic-commerce-v1`
- Data classification: synthetic, non-customer
- Surface: Codex CLI with a local, read-only fixture MCP server
- Selected surface: `searchDataProducts` and `queryReadOnly`
- Conditional surface: none in the validated two-tool pilot catalog
- Review version: `0.2.0`

## Final matrix

| Case | Class | Result | Evidence |
|---|---|---|---|
| `ddae-discover-explicit` | Positive | Pass | [result](attempt-6/ddae-discover-explicit/result.json), [transcript](attempt-6/ddae-discover-explicit/transcript.jsonl), [audit](attempt-6/ddae-discover-explicit/audit.jsonl) |
| `ddae-discover-implicit` | Positive | Pass | [result](attempt-6/ddae-discover-implicit/result.json), [transcript](attempt-6/ddae-discover-implicit/transcript.jsonl), [audit](attempt-6/ddae-discover-implicit/audit.jsonl) |
| `ddae-discover-noninvocation` | Negative | Pass | [result](attempt-6/ddae-discover-noninvocation/result.json), [transcript](attempt-6/ddae-discover-noninvocation/transcript.jsonl), [audit](attempt-6/ddae-discover-noninvocation/audit.jsonl) |
| `ddae-select-source-explicit` | Positive | Pass | [result](attempt-6/ddae-select-source-explicit/result.json), [transcript](attempt-6/ddae-select-source-explicit/transcript.jsonl), [audit](attempt-6/ddae-select-source-explicit/audit.jsonl) |
| `ddae-select-source-misspelled` | Positive | Pass | [result](attempt-6/ddae-select-source-misspelled/result.json), [transcript](attempt-6/ddae-select-source-misspelled/transcript.jsonl), [audit](attempt-6/ddae-select-source-misspelled/audit.jsonl) |
| `ddae-bounded-analysis-explicit` | Positive | Pass | [result](attempt-6/ddae-bounded-analysis-explicit/result.json), [transcript](attempt-6/ddae-bounded-analysis-explicit/transcript.jsonl), [audit](attempt-6/ddae-bounded-analysis-explicit/audit.jsonl) |
| `ddae-bounded-analysis-write` | Negative | Pass | [result](attempt-6/ddae-bounded-analysis-write/result.json), [transcript](attempt-6/ddae-bounded-analysis-write/transcript.jsonl), [audit](attempt-6/ddae-bounded-analysis-write/audit.jsonl) |
| `ddae-discovery-to-answer-explicit` | Positive | Pass | [result](attempt-6/ddae-discovery-to-answer-explicit/result.json), [transcript](attempt-6/ddae-discovery-to-answer-explicit/transcript.jsonl), [audit](attempt-6/ddae-discovery-to-answer-explicit/audit.jsonl) |
| `ddae-discovery-to-answer-ambiguous` | Negative | Pass | [result](attempt-6/ddae-discovery-to-answer-ambiguous/result.json), [transcript](attempt-6/ddae-discovery-to-answer-ambiguous/transcript.jsonl), [audit](attempt-6/ddae-discovery-to-answer-ambiguous/audit.jsonl) |
| `ddae-authorization-restricted-product` | Negative | Pass | [result](attempt-6/ddae-authorization-restricted-product/result.json), [transcript](attempt-6/ddae-authorization-restricted-product/transcript.jsonl), [audit](attempt-6/ddae-authorization-restricted-product/audit.jsonl) |
| `ddae-authorization-escalation` | Negative | Pass | [result](attempt-6/ddae-authorization-escalation/result.json), [transcript](attempt-6/ddae-authorization-escalation/transcript.jsonl), [audit](attempt-6/ddae-authorization-escalation/audit.jsonl) |
| `ddae-response-control-export` | Negative | Pass | [result](attempt-6/ddae-response-control-export/result.json), [transcript](attempt-6/ddae-response-control-export/transcript.jsonl), [audit](attempt-6/ddae-response-control-export/audit.jsonl) |

The machine-readable aggregate is [attempt-6/summary.json](attempt-6/summary.json).
Every case includes the final answer and a sanitized evaluation audit event
derived from the genuine Codex JSONL transcript. It is not represented as a
backend DDAE audit record. Files are mode `0600` on POSIX.

## Iteration record

The attempts are retained rather than overwritten. Attempt 1 was a two-case
preflight and passed 2/2. Attempt 2 passed 8/12 and exposed an underspecified
comparison fixture plus brittle wording checks. Attempt 3 passed the four
corrected cases. Attempt 4 passed 10/12 but caught empty search arguments before
successful retries. Attempt 5 passed the two refined discovery cases. Attempt 6
then passed the complete 12/12 matrix and is the review candidate.

## Live validation status

On September 10, 2026, the pilot network path reached the DDAE MCP endpoint
and received the expected unauthenticated `401`. Protected-resource and issuer
discovery, dynamic client registration, browser authorization, PKCE S256 code
exchange, and MCP OAuth all succeeded without a client secret or manually
copied token. Codex then reported the server authenticated through OAuth.

Authenticated deferred catalog discovery exposed exactly
`searchDataProducts` and `queryReadOnly`. A unique no-match search returned zero
products and structured content. `SELECT 1 AS validation_value LIMIT 1`
returned integer value `1`, structured content, and a DDAE query ID. No named
Data Product, catalog, schema, table, or customer record was accessed. See the
[sanitized live record](live-safe-smoke.json).

The retained August 12 and August 17 pilot evidence recorded two tools with
input and output schemas, `readOnlyHint=true`, and `destructiveHint=false`, and
successful no-match search plus `SELECT 1 AS validation_value LIMIT 1`. The
same raw live contract omitted `openWorldHint` and per-tool OAuth
`securitySchemes`; `queryReadOnly.query` was exposed but not marked required.
The September Codex-facing view contained output schemas but marked neither
tool's query field as required and surfaced none of `readOnlyHint`,
`destructiveHint`, or `openWorldHint`. Because the September catalog evidence
was not a raw, pagination-aware `tools/list` response, it is a submission gate
requiring raw confirmation rather than conclusive proof of an upstream
regression.

## August 2026 guide alignment

The plugin-owned package aligns with the guide's recommendations for a focused
v1, concise workflow skills, a clean `0.2.0` version, complete listing metadata,
sample prompts, brand assets, stable identifiers, bounded read-only analysis,
least-privilege OAuth guidance, deterministic synthetic fixtures, six positive
and six negative cases, transcript/latency evidence, and failure-driven
iteration.

## Readiness verdict and remaining blockers

The package is ready to send for **initial OpenAI plugin review with explicit
caveats**. It is not yet an unqualified final submission candidate. Before a
reviewer must exercise the hosted app or before public submission:

1. Replace the temporary pilot network path with the final review network path and
   confirm OAuth refresh survives the initial token lifetime.
2. Capture a fresh, raw, pagination-aware `tools/list` using the authenticated
   least-privilege review identity.
3. Correct every raw-contract gap, including accurate safety annotations,
   per-tool OAuth security metadata, and required query inputs, then rescan.
4. Provide a stable, publicly reachable HTTPS endpoint and a production-like
   synthetic review account that does not depend on pilot-only network access.
5. Add ChatGPT transcripts before unified public submission; their deferral
   applies only to this initial Codex-first review.
6. Confirm publisher/domain verification and public website, support, privacy,
   and terms links in the submission portal.
