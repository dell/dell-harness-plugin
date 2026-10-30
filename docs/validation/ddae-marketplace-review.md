# Dell Data Analytics Engine: marketplace review record

Review and submission material for the `dell-data-analytics-engine` plugin. It is
kept separate from the customer README because it describes how the package was
validated, not how customers use it.

## Package boundary

In a marketplace submission, attach the skills to the reviewed Universal MCP URL
or approved URL template; do not put a customer-specific URL in the package.

## Review authentication handoff

The review deployment uses the same MCP-native OAuth flow as production. Give
the reviewer a non-administrative test user in a production-like realm and
grant it access only to the versioned synthetic fixture. Do not send an access
token, refresh token, password, client secret, or customer URL in the plugin
archive or review notes.

Before submission, verify all of the following from the final hosted endpoint:

- protected-resource and issuer discovery complete successfully;
- DCR creates a native public client with PKCE S256 and no client secret;
- the reviewer sees the intended tool catalog and synthetic Data Products;
- a no-match search and `SELECT 1 AS validation_value LIMIT 1` succeed;
- a fresh sanitized contract scan has complete schemas and annotations; and
- at least five positive and three negative hero cases have retained ChatGPT
  and Codex transcript references, latency, and audit-event evidence.

The August 2026 contract scan remains useful provenance, but it is not final review
evidence. Its missing `openWorldHint` annotations and optional `query` field are
server-owned contract gaps that must be corrected on the reviewed endpoint or
by an approved MCP gateway before submission.

An authenticated September 10, 2026 Codex revalidation completed OAuth and
successfully executed a unique no-match Data Product search plus
`SELECT 1 AS validation_value LIMIT 1`; both returned structured output. The
Codex-facing catalog exposed exactly `searchDataProducts` and `queryReadOnly`,
but marked neither tool's query input as required and did not surface any of
the three safety hints. Because that catalog view was not a raw,
pagination-aware `tools/list` capture, run the contract-capture command below
before attributing the discrepancy to the upstream server or submitting it.

From a machine that can reach the DDAE endpoint, with the Codex-managed OAuth
session enrolled, capture and grade a fresh sanitized contract from the
marketplace repository root:

~~~bash
python scripts/capture_remote_mcp_contract.py \
  ddae /tmp/ddae-tools.json
python scripts/aidp_eval.py contract \
  dell-data-analytics-engine /tmp/ddae-tools.json --review-ready
~~~

## Codex-first initial review evidence

The deterministic local review fixture contains only synthetic, non-customer
data and exposes the two selected read-only tools. The final September 10, 2026
Codex run passed all 12 selected cases: six positive and six negative. It covers
explicit, implicit, misspelled, ambiguous, authorization, write-refusal,
unbounded-export, and non-invocation behavior. ChatGPT transcripts are deferred
for the initial review and are not claimed as complete.

~~~bash
python scripts/aidp_eval.py fixture dell-data-analytics-engine
python scripts/run_ddae_codex_evals.py \
  evals/results/ddae-codex-initial-review-20260910/attempt-6
~~~

See [the review record](../../evals/results/ddae-codex-initial-review-20260910/REVIEW.md)
and [machine-readable summary](../../evals/results/ddae-codex-initial-review-20260910/attempt-6/summary.json).
The approved live smoke is recorded in
[live-safe-smoke.json](../../evals/results/ddae-codex-initial-review-20260910/live-safe-smoke.json).
Local fixture and live smoke evidence do not replace a raw contract
scan and reviewer-network test against the final hosted MCP endpoint.
