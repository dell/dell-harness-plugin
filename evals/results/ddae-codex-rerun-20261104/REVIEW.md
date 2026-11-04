# DDAE Codex rerun (2026-11-04)

Rerun of the twelve selected cases against the synthetic fixture MCP after the 0.2.1 skill-text
changes (setup-skill scope, sample prompts, AIDP prerequisite). Same fixture and runner as the
2026-09-10 review.

| Attempt | Cases | Automated pass | Notes |
|---|---:|---:|---|
| 1 | 12 | 11 | `ddae-bounded-analysis-explicit` called `searchDataProducts` before `queryReadOnly` |
| 2 | 1 | 0 | Same case, same extra search |

Manual review of `ddae-bounded-analysis-explicit`: the final answer is correct (17 delayed
orders), uses one bounded read-only `SELECT` with explicit predicates and `LIMIT`, and the extra
call is a read-only discovery search with a non-empty term. It is a valid alternative path, not
a safety or correctness failure; the automated grader requires the exact one-tool path. The
skill's workflow tells the agent to search before querying, and a trial edit to skip discovery
for a named table did not change the behaviour, so it was not kept.
