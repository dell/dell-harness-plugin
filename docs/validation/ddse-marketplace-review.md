# Dell Data Search Engine: marketplace review record

Review and submission material for the `dell-data-search-engine` plugin. It is
kept separate from the customer README because it describes how the package was
validated, not how customers use it.

## Package boundary

In a marketplace submission, attach the skills to the reviewed Universal MCP URL
or approved URL template; do not put a customer-specific URL in the package.

## Review authentication handoff

Create a dedicated, expiring review API key rather than reusing a shared or
administrator credential. Bind it to the review Kibana space, synthetic index
patterns, and the minimum application privileges needed by the submitted tool
surface. Deliver the complete `ApiKey ...` authorization value through the
review portal's protected credential mechanism; never place it in the plugin
archive, prompts, transcripts, screenshots, or source control.

Before submission, verify all of the following from the final hosted endpoint:

- TLS and the exact Kibana Agent Builder MCP URL work from the review runtime;
- the review key sees only the intended space, indices, and read-only tools;
- every submitted tool has typed input and output schemas plus
  `readOnlyHint`, `destructiveHint`, and `openWorldHint` annotations;
- inference-dependent and installed-content tools are either provisioned and
  fixture-tested or removed from the submitted v1 surface; and
- at least five positive and three negative hero cases have retained Codex
  transcript references, latency, and audit-event evidence. ChatGPT transcripts
  are deferred for the initial review and remain a later review artifact.

The authenticated September 10, 2026 scan confirms that the native 25-tool
server still omits output schemas and safety annotations. Those are server-owned
contract gaps. Correct them in the reviewed DDSE release or through an approved
MCP gateway; skill instructions cannot substitute for tool metadata.

A separate sanitized live smoke then exercised only synthetic nonexistent names
and an index-free constant ES|QL expression. Index discovery, missing-index
mapping, and constant ES|QL execution succeeded in 120 ms, 119 ms, and 384 ms;
an omitted required mapping argument correctly returned a tool error in 130 ms.
Every response contained one text content item and no `structuredContent`, which
independently confirms the upstream structured-output gap. Raw tool content was
neither printed nor retained.

From a machine that can reach the DDSE endpoint, with the API-key authorization
exported only in the process environment, capture and grade a fresh sanitized
contract from the marketplace repository root:

~~~bash
python scripts/capture_remote_mcp_contract.py \
  ddse /tmp/ddse-tools.json
python scripts/aidp_eval.py contract \
  dell-data-search-engine /tmp/ddse-tools.json --review-ready
~~~

After contract capture, run the non-sensitive execution smoke separately. Pass
`--ca-bundle` only when the deployment uses a private certificate authority:

~~~bash
python scripts/run_ddse_safe_smoke.py \
  /tmp/ddse-safe-smoke.json \
  --ca-bundle <customer-ca-bundle>
~~~

The smoke report is mode `0600` on POSIX and contains only response shape,
outcome, and latency—not raw search results, index names, or credentials.

## Initial-review tool policy

The Codex-first initial review selects 10 deterministic tools: index listing,
mapping, exact ES|QL, known-document retrieval, service and dependency lookup,
trace metrics, correlated logs, log-rate analysis, and log categories. Six
positive and three negative synthetic cases cover that surface. The other 15
advertised tools are conditional and are not used by the selected cases.

The three Agent Builder inference tools remain documented but out of the
default path until a default chat connector is positively validated in the
same Kibana space. Installed-content, embedding, ML, case, workflow, alert,
host, change-point, index-info, and security tools likewise remain conditional
until their individual data, license, privilege, and fixture prerequisites are
validated. Because filtering is workflow-level, these tools may still appear
in the upstream Elastic `tools/list`; the plugin does not claim to remove them
from the server.

For the initial review, genuine Codex JSONL transcripts and sanitized audit
events derived from those transcripts are required. ChatGPT transcript capture
is explicitly deferred and is not counted as an initial-review failure.
Validate the fixture contract and run the Codex matrix from the repository root:

~~~bash
python scripts/aidp_eval.py fixture dell-data-search-engine
python scripts/run_ddse_codex_evals.py \
  evals/results/ddse-codex-initial-review
~~~

The September 10, 2026 final Codex run passed all nine selected cases (six
positive and three negative). Its sanitized evidence index and per-case JSONL
transcripts are in
`evals/results/ddse-codex-initial-review-20260910/REVIEW.md` and the final
attempt directory linked from that index.
This closes the local fixture-evidence gap; it does not close the upstream
output-schema and safety-annotation gaps described above.
