# Changelog

All notable changes to this repository are documented here. The format is
loosely based on [Keep a Changelog](https://keepachangelog.com/). Each release
heading lists the plugin versions it ships; plugins are tagged
`<plugin>/v<version>`.

## Unreleased: `dell-data-orchestration-engine` 0.6.0, `dell-data-analytics-engine` 0.2.1, `dell-data-search-engine` 0.2.1, `dell-ai-factory-agentic-workspace` 0.5.1

### Security

- `dell-data-orchestration-engine` 0.6.0: every project-level lookup (models,
  services, triggers, executions, a model's own dataset, and the datasets and
  models written into RAG code nodes) now rejects a resource owned by another
  project before any read or write. Previously only dataset and pipeline lookups
  were checked. A convention test fails on any new unchecked lookup.

### Changed

- MCP safety labels are corrected and pinned for all 72 DOE tools: deleting an
  exact target and overwriting state in place are destructive and idempotent,
  pausing is idempotent, and marketplace reads and installs are open-world. Every
  profile now advertises all four hints.
- `model_list` joins the default `plugin-v1` profile (43 tools) so the RAG
  workflow can find its embedding model.
- DOE skills and advertised tool text name only tools the default profile has,
  no longer ask the agent to switch profiles, and teach the pipeline stop path:
  pause, confirm it is no longer installed, then check for in-flight runs.
- Every skill's sample prompt is a concrete request instead of the
  "Use $skill to ..." template; the skill linter rejects the template.
- DDAE and DDSE READMEs and setup skills state that a deployed Dell AI Data
  Platform instance with the vendor MCP server is required.
- Every plugin's square listing icon is now built from the brand-approved Dell
  Technologies logo (`scripts/make_square_icon.py PATH_TO_LOGO`).
- The DOE setup skill checks `python --version` before first use and gives the
  Windows, macOS and Linux fix when `python` is missing or older than 3.11.

### Fixed

- `configure_dataloop.py --check` reports the launcher's own read-only
  preflight: the same precedence (environment over file), the same errors, the
  source of every setting, the exact command line, and the server version a
  configured `command` runs. It previously reported a ready command that the
  launcher then refused.
- The DOE package ships `docs/eval.md` and the workflow guides the code cites.
- The RAG golden trace fails when `rag_query` leaves its temporary item or
  vector behind.

## 2026-10-30: `dell-ai-factory-agentic-workspace` 0.5.0, `dell-data-orchestration-engine` 0.5.0

### Changed

- **Breaking:** `dell-devspaces-sandbox` is renamed to
  `dell-ai-factory-agentic-workspace` 0.5.0, with display name
  **Dell AI Factory Agentic Workspace**. The plugin folder, marketplace entry,
  and skill invocation (`$manage-devspaces-sandbox` →
  `$manage-agentic-workspace`) change accordingly. Run
  `codex plugin remove dell-devspaces-sandbox@dell-harness-plugin`, then
  install `dell-ai-factory-agentic-workspace@dell-harness-plugin`. The
  `dell_devspaces` MCP server name, tool names, environment variables, and
  runtime cache location are unchanged. Earlier entries for this plugin use the
  former ID `dell-devspaces-sandbox`.
- **Breaking:** Python 3.11 is the minimum for every plugin and repository
  script. 3.10 never worked for the connector server or the evaluation scripts.
- `dell-data-orchestration-engine` 0.5.0: the launcher prepares a reusable
  isolated runtime outside the plugin cache (override with
  `DATALOOP_RUNTIME_DIR`) when the starting `python` cannot import the bundled
  server, so its `mcp` 2.0 pin no longer conflicts with Agentic Workspace's
  `mcp` 1.30. On Windows it keeps a waiting parent instead of `execve`.
- `requirements.toml` keys plugin-bundled servers under
  `plugins.<plugin>.mcp_servers` and matches the launcher script argument.
- DDAE, DDSE and DOE listings link to the Dell AI Data Platform page, and every
  manifest declares its repository, privacy and terms URLs.
- Marketplace-review material moved from the DDAE and DDSE READMEs and skills
  to `docs/validation/ddae-marketplace-review.md` and
  `docs/validation/ddse-marketplace-review.md`.
- The Agentic Workspace TLS docs state that a certificate retry leaves the rest
  of the connector session unverified, and `connect_devspace` is documented as
  the one write-gate exception because it changes only local state.

### Added

- Continuous integration (`.github/workflows/ci.yml`) on Python 3.11 and 3.13:
  skill lint, repository tests, the Agentic Workspace and Data Orchestration
  Engine suites, gitleaks, and REUSE. Repository test dependencies are in
  `requirements-dev.txt`.
- `scripts/validate_skills.py`, a skill linter enforced by
  `tests/plugins/test_skill_lint.py`. Every skill needs matching frontmatter,
  a "Do not use for ..." routing exclusion, an `agents/openai.yaml` adapter,
  no template placeholders, at most 200 lines, and a line treating tool output
  as untrusted data.
- Static routing guards (`test_skill_routing_boundaries.py`) and
  release-consistency checks (`test_release_consistency.py`) tying manifests,
  marketplace, changelog, and docs together.
- `scripts/record_run_metadata.py` and `docs/validation/VALIDATION_PLAN.md` for
  installed-plugin validation runs.
- Square listing icons (`dell-technologies-icon-square.png`) for every plugin.
  Replace with the brand-approved icon when available.

### Fixed

- `configure_dataloop.py` writes the API key through a user-only temporary file
  and atomically replaces the configuration, closing a window in which the key
  was world-readable. `--profiles all` combined with other profiles is rejected.
- Every skill tells the agent to treat tool output as data, never as
  instructions.

### Removed

- The duplicate `mcp-server/scripts/doe_state_dir.py` (the plugin's
  `scripts/dataloop_state_dir.py` is the single helper) and a dead duplicate of
  the DOE `plugin-v1` server instructions. The DDAE and DDSE Codex evaluation
  runners share `scripts/codex_eval_runner.py`.

## 2026-10-27: `dell-devspaces-sandbox` 0.4.1

### Changed

- `dell-devspaces-sandbox` 0.4.1: delegate SSH configuration and host-key policy
  to the CLI instead of inserting strict-checking overrides. Reconnecting removes
  only complete legacy plugin-owned guards for the selected alias, preserving
  user policies and CLI Includes. CLI policies that disable host-key checking
  no longer require separate plugin key enrollment and do not verify server
  identity. Tunnel readiness remains separate from SSH login and Codex status.

## 2026-10-26: `dell-devspaces-sandbox` 0.4.0

### Changed

- `dell-devspaces-sandbox` 0.4.0: reusable isolated Python/MCP bootstrap and a
  bundled retained client replace per-chat dependency/client generation. New
  `begin_devspaces_authentication` and `get_devspaces_authentication_progress`
  tools initiate login during requested setup and report safe completion without
  terminal readback. Effective TLS mode carries into workspace/tunnel checks.
  With automatic retry enabled, an inconclusive empty inventory gets one
  differential TLS read; retain the exception only when it verifies the cluster.
  Setup completes bounded login diagnostics before account-assignment questions.
  Independent end-to-end testing added automatic preservation of pinned SSH trust
  before connector-owned Includes, no-newline readiness detection, safe retained-
  client error categories, startup status, and duplicate-request protection.
- `dell-devspaces-sandbox` 0.3.0: agent-operated native CLI authentication uses
  the machine browser, with bundled Python/PowerShell/batch helpers and CLI-only
  workspace validation. The batch launcher now accepts `--connector` and
  `--cluster`; PowerShell wrapper argument names remain available. Certificate
  failures in read-only list checks retry once per connector session with
  verification disabled by default; set `DEVSPACES_AUTO_TLS_RETRY=false` to
  require verified TLS. Runtime adds `automatic_tls_retry` and reports the
  effective TLS state. Existing tool names and inputs are unchanged.

## 2026-10-14: public Apache-2.0 release preparation, `dell-data-orchestration-engine` 0.4.0

### Security

- Closed the residual release-review items: removed lab network-access
  instructions (SOCKS proxy, host overrides, private-tunnel wording) and lab
  Keycloak values from the DDAE/DDSE docs and evaluation records, dropped
  private-helper names from `.gitignore`, and made the DOE test suite hermetic
  (real Dataloop requests are blocked). Validation evidence no longer records
  local filesystem paths. The Dataloop launcher now executes the resolved
  command and rejects a group- or world-writable parent directory. The
  release-hygiene test also guards against these regressions.
- Remediated the 2026-10-08 static-analysis scan (3 critical, 8 medium, 4 low).
  The Codex evaluation runners send prompts on stdin (`codex exec -`) instead
  of argv, validate case IDs, prompts and invocations, confine output
  directories, and require an absolute `codex` path. The Dataloop launcher
  accepts only an absolute, non-group/world-writable `DATALOOP_MCP_COMMAND`,
  requires a user-owned configuration file, and executes without a `PATH`
  search. URL validation no longer reads embedded credentials, label colours use
  SHA-256, the token-file warning omits local paths, and test fixtures generate
  their secrets at runtime.

### Added

- `dell-data-orchestration-engine` 0.4.0: synchronized the bundled MCP server
  with the upstream DOE MCP server as of 2026-09-21. The `plugin-v1` profile
  grows from 36 to 42 tools (`item_get`, `annotation_get`, `pipeline_list`,
  `pipeline_pause`, `pipeline_delete`, `model_delete_subset`). Upstream also
  brings verified cleanup of temporary `rag_query` items and vectors
  (`query_cleanup`), allowlisted `pii_report` samples, project-ownership checks
  on lookups, and reuse of an identical existing pipeline. A new
  inspect-and-rollback evaluation workflow covers the added tools.
- `LICENSE`, `NOTICE`, `CONTRIBUTING.md` (with DCO sign-off),
  `CODE_OF_CONDUCT.md`, `SECURITY.md`, `MAINTAINERS.md`, and GitHub issue and
  pull request templates.
- Release-hygiene tests that reject non-public hostnames, internal-distribution
  wording, non-Apache manifest licenses, and missing copyright headers.
- A `.gitleaks.toml` secret-scanning configuration.
- `REUSE.toml` and `LICENSES/Apache-2.0.txt`; the repository passes
  `reuse lint` (REUSE Specification 3.3).

### Changed

- Licensed the repository under Apache-2.0. Plugin manifests now declare
  `"license": "Apache-2.0"`, and every source file carries an SPDX copyright
  header.
- `dell-devspaces-sandbox`: `DEVSPACES_CLUSTER_URL` is now required and has no
  built-in default. Until it is set, every remote tool fails closed without
  invoking the connector.
- `requirements.toml` is now an example allowlist covering only the four
  plugins in this repository.
- Documentation, fixtures, and evaluation cases use reserved example hostnames.

## 2026-08-26: `dell-data-analytics-engine` 0.2.0, `dell-data-search-engine` 0.2.0

### Changed

- `dell-data-analytics-engine` 0.2.0 and `dell-data-search-engine` 0.2.0:
  documented DDAE OAuth and aligned plugin versions.
