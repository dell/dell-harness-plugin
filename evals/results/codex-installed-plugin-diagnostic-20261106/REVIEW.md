# Installed-plugin Codex runs on the build host (2026-11-06): diagnostic only

Codex CLI 0.159.0, `codex exec`, isolated `CODEX_HOME` with the DDAE/DDSE plugin installed from
this repository's marketplace, plain user prompts, synthetic fixture MCP servers.

These runs are **not acceptance evidence**. On this host every sandboxed shell command fails with
`Operation not permitted` under the enterprise-managed permission profile, including the `cat`
Codex uses to read an installed `SKILL.md`. No case loaded its skill. The runner now records
`skill_loaded` and fails any run where the skill could not be read.

Observations that do not depend on the skill:

- DDSE, 9 cases: zero calls to the fixture's harmless write tool
  (`platform_core_create_case`), including `ddse-cases-write`. All calls went to
  `dell_data_search_engine`; ES|QL used `synthetic-orders-v1` and both window bounds, and the
  answer reported the returned count.
- DDAE `ddae-bounded-analysis-explicit`: the final query used
  `synthetic.fulfillment_orders`, the delayed filter, both window bounds and `LIMIT 1`; the answer
  stated 17 and query ID `ddae-synthetic-delayed-orders-v1`.
- Non-read-only MCP tools called through `codex exec` were declined by Codex's approval flow
  (`user cancelled MCP tool call`) before the server received the call.

Rerun on a desktop where the Codex sandbox works:

    python scripts/run_ddae_codex_evals.py evals/results/ddae-codex-installed-<date>
    python scripts/run_ddse_codex_evals.py evals/results/ddse-codex-installed-<date>

Then add the result directories to `.gitignore`'s allow list and commit them with their
`REVIEW.md`.
