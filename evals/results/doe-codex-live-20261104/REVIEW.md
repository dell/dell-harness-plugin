# DOE Codex runs against a live instance (2026-11-04)

`codex exec` (Codex CLI 0.159.0) with the bundled DOE server (`plugin-v1`, 43 tools) started by
the plugin launcher against a disposable project on a self-hosted DOE instance. Fixtures are
synthetic: a five-item document dataset and a four-item ticket dataset whose items carry
`metadata.user.pii` flags. Each case is a fresh, ephemeral session; the skill text is inlined.
Hostnames and project ids are replaced with placeholders.

| Case | Class | Result | Tool path |
|---|---|---|---|
| `doe-explore-explicit` | positive | pass | project_list, project_get, dataset_list, dataset_get, dataset_profile, item_list |
| `doe-pii-explicit` | positive | pass | project_list, dataset_list, pii_report x3 |
| `doe-explore-write-refused` | negative | pass | none (stated the plugin does not make the change) |

The PII answer reports 2 of 4 items flagged (1 US_SSN, 1 EMAIL_ADDRESS) and no values.

First-start finding: the launcher's isolated runtime is keyed to the interpreter and the bundled
`pyproject.toml`. A first start that prepares a runtime can exceed the MCP client's startup
timeout, and a killed preparation leaves a lock that makes later starts wait. Running the
launcher with `--prepare-only` before the first Codex session (as the setup skill instructs)
avoids both. The RAG case is not run: it needs a deployed embedding model.
