# Dell Data Orchestration Engine plugin initial review

Date: 2026-09-11

## Scope and verdict

The reviewed package is `dell-data-orchestration-engine` (DOE), the Dataloop
orchestration plugin sometimes referred to informally as DDOE.

- **Initial OpenAI source/package review:** ready with caveats.
- **Hands-on reviewer workflow validation:** not ready until the synthetic
  review tenant, scoped API key, and retained Codex transcripts exist.
- **Unified public submission:** not ready. The current MCP server is local
  `stdio`, uses a Dataloop API key, and has no hosted HTTPS/OAuth reviewer path.

This assessment uses the criteria in
`docs/[EXT] How to Build a Great Plugin - Aug 2026.pdf`.

## Best-practice assessment

| Guide criterion | Result | Evidence or caveat |
| --- | --- | --- |
| Focused v1 capability set | Pass with concern | The plugin defaults to 36 reviewed tools rather than the full 70-tool development surface. The default is still large at 19,511 `o200k_base` tokens and has only 289 tokens of budget headroom. |
| Clear typed schemas and structured outputs | Pass | Review-ready contract validation found no missing input schemas, output schemas, or generic object outputs. |
| Read/write separation and annotations | Pass | Every default tool exposes `readOnlyHint`, `destructiveHint`, and `openWorldHint`; read-only tools also expose `idempotentHint=true`. |
| Stable IDs and bounded responses | Pass | Tool contracts and skills require resolved project/resource IDs, bounded lists, pagination, and explicit truncation handling. |
| Workflow-specific skills | Pass | Six skills validate and cover configuration, dataset exploration, RAG, PII, classifiers, and pipeline operations. |
| Five to ten hero workflows | Pass at specification level | Seven workflows and 21 variants are defined. The selected set has 11 positive and 7 negative cases; three secondary variants are not selected. |
| Explicit, implicit, ambiguous, misspelled, and non-invocation prompts | Pass at specification level | All invocation classes are represented in the selected fixture contract. |
| Repeatable fixtures, transcripts, and grading | Partial | The deterministic synthetic fixture and strict grader validate. Actual selected Codex transcripts, final answers, latency, and audit records have not been captured. |
| Authentication and reviewer access | Blocked | Local configuration is missing. OAuth is explicitly not implemented. No least-privilege review account or public reviewer endpoint is available. |
| Listing metadata and assets | Pass for local package | Manifest includes clean version `0.3.0`, value proposition, owner links, policy URLs, prompts, brand color, and Dell assets. Portal/domain ownership still requires reviewer confirmation. |
| Same plugin across ChatGPT and Codex | Not yet demonstrated | ChatGPT transcripts are deferred for the initial internal review and remain required before a unified public submission. |

## Validation evidence

### Package and skills

- Plugin manifest validator: passed.
- Six skill validators: passed.
- DOE plugin integration tests: 6 passed.
- Ruff: passed.
- Mypy: passed for all 40 DOE source and script files.
- Diff integrity: passed.

### MCP server

- Complete isolated suite: **620 passed, 1 expected failure**.
- The expected failure tracks trigger-to-execution outcome correlation in the
  70-tool development surface. Trigger tools are not in the 36-tool plugin-v1
  surface, so this does not block the reviewed default.
- Review-ready contract check: passed.
- Exact default contract: 36 tools; zero missing input schemas, output schemas,
  required annotations, or read-only idempotency annotations.
- Sanitized snapshot: `contract.json`.

### Context and workflow cost

- Default plugin-v1 surface: 19,511 `o200k_base` tokens.
- Internal ceiling: 19,800 tokens.
- Standing optimization target: 13,300 tokens.
- Dataset exploration fixture: 4 tool turns.
- PII assessment fixture: 7 tool turns.
- Agentic RAG fixture: 18 tool turns.

These measurements prove deterministic server behavior and cost; they are not
model-selection transcripts.

### Evaluation contract

- Seven hero workflows.
- 21 total variants.
- 18 selected cases: 11 positive and 7 negative.
- Three secondary variants retained outside the initial selected set.
- 36 selected tools, all covered by a selected positive tool path.
- Synthetic non-customer fixture validation: passed.
- ChatGPT transcripts: deferred for the initial internal review.
- Golden-trace criteria regenerate without drift from the committed baseline.

## Changes made during this review

- Normalized the plugin version to `0.3.0`.
- Registered the DOE synthetic fixture with the shared review harness.
- Added an explicit submission policy and selected-case inventory.
- Promoted existing misspelling, PII-writeback, and exact-execution-termination
  cases so every selected tool has positive coverage.
- Made static analysis ignore installed third-party package internals while
  continuing to check every local DOE source and script file.
- Added fixture validation to the documented release commands.

## Remaining gates

1. Enroll a least-privilege Dataloop API key locally with
   `configure_dataloop.py`; never paste it into chat or commit it.
2. Provision the documented disposable `synthetic-pilot` project and fixture
   data.
3. Run the 18 selected cases through Codex and retain prompt, selected skill,
   tool arguments/results, confirmation exchange, final answer, latency, and
   audit evidence.
4. Include at least one approved write, one destructive exact-target action,
   one PII-exfiltration refusal, and one unrelated non-invocation in the live
   evidence.
5. Reduce the 19,511-token default surface or obtain explicit review acceptance
   for the current tool budget.
6. For final public submission, provide a stable HTTPS MCP endpoint, OAuth or
   another approved delegated-auth path, a reviewer account, and ChatGPT
   transcripts.
7. Confirm publisher-domain ownership and support/privacy/terms destinations in
   the submission portal.

No customer data, live Dataloop records, credentials, or billable operations
were used in this package-level review.
