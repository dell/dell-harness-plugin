# Changelog

All notable changes to this repository are documented here. The format is
loosely based on [Keep a Changelog](https://keepachangelog.com/).

## Unreleased

### Security

- Closed the residual release-review items: removed pilot network-access
  instructions (SOCKS proxy, host overrides, private-tunnel wording) and pilot
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

- `scripts/record_run_metadata.py`: records the package hash, versions, effective
  profile, tool-catalog hash, model, effort, fixture and limits for a validation
  run, and validates per-case results so run status stays separate from quality.
- `docs/validation/VALIDATION_PLAN.md`: the ten installed-plugin case families
  from the 2026-09-17 technical feedback, with environment, acceptance evidence,
  current offline coverage, and a results log. Cases needing a live environment
  are marked `not run`.
- Square listing icons (`dell-technologies-icon-square.png`, built by
  `scripts/make_square_icon.py` from the existing wordmark) and a test that every
  manifest icon is a square PNG. Replace with the brand-approved icon when available.

- Continuous integration (`.github/workflows/ci.yml`): repository tests, the
  Dev Spaces and Data Orchestration Engine suites, gitleaks, and REUSE.
- `scripts/validate_skills.py`, a skill linter enforced by
  `tests/plugins/test_skill_lint.py`. Every skill needs matching frontmatter,
  a "Do not use for ..." routing exclusion, an `agents/openai.yaml` adapter,
  no template placeholders, and at most 200 lines.
- Static routing guards (`test_skill_routing_boundaries.py`) so the DDAE and
  DDSE skills exclude each other and positive evaluation prompts do not leak
  another product's vocabulary, and release-consistency checks
  (`test_release_consistency.py`) tying manifests, marketplace, changelog, and
  docs together.

- `dell-data-orchestration-engine` 0.4.0: synchronized the bundled MCP server
  with the upstream DOE MCP server as of 2026-09-21. The `plugin-v1` profile
  grows from 36 to 42 tools (`item_get`, `annotation_get`, `pipeline_list`,
  `pipeline_pause`, `pipeline_delete`, `model_delete_subset`). Upstream also
  brings verified cleanup of temporary `rag_query` items and vectors
  (`query_cleanup`), allowlisted `pii_report` samples, project-ownership checks
  on lookups, and reuse of an identical existing pipeline. A new
  inspect-and-rollback evaluation workflow covers the added tools.

### Changed

- **Breaking:** `dell-devspaces-sandbox` is renamed to
  `dell-ai-factory-agentic-workspace` (0.5.0), with display name
  **Dell AI Factory Agentic Workspace**. The plugin folder, marketplace entry,
  and skill invocation (`$manage-devspaces-sandbox` →
  `$manage-agentic-workspace`) change accordingly. Run
  `codex plugin remove dell-devspaces-sandbox@dell-harness-plugin`, then
  install `dell-ai-factory-agentic-workspace@dell-harness-plugin`. The
  `dell_devspaces` MCP server name, tool names, environment variables, and
  runtime cache location are unchanged. Earlier entries for this plugin use the
  former ID `dell-devspaces-sandbox`.

- `dell-devspaces-sandbox` 0.4.1: delegate SSH configuration and host-key policy
  to the CLI instead of inserting strict-checking overrides. Reconnecting removes
  only complete legacy plugin-owned guards for the selected alias, preserving
  user policies and CLI Includes. CLI policies that disable host-key checking
  no longer require separate plugin key enrollment and do not verify server
  identity. Tunnel readiness remains separate from SSH login and Codex status.

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

- Added missing routing exclusions to five skills and `agents/openai.yaml`
  adapters to the DDAE and DDSE `configure-*` skills, found by the new linter.

- Licensed the repository under Apache-2.0. Plugin manifests now declare
  `"license": "Apache-2.0"`, and every source file carries an SPDX copyright
  header.
- `dell-devspaces-sandbox`: `DEVSPACES_CLUSTER_URL` is now required and has no
  built-in default. Until it is set, every remote tool fails closed without
  invoking the connector.
- `requirements.toml` is now an example allowlist covering only the four
  plugins in this repository.
- Documentation, fixtures, and evaluation cases use reserved example hostnames.

### Removed

  entries.

### Added

- `LICENSE`, `NOTICE`, `CONTRIBUTING.md` (with DCO sign-off),
  `CODE_OF_CONDUCT.md`, `SECURITY.md`, `MAINTAINERS.md`, and GitHub issue and
  pull request templates.
- Release-hygiene tests that reject non-public hostnames, internal-distribution
  wording, non-Apache manifest licenses, and missing copyright headers.
- A `.gitleaks.toml` secret-scanning configuration.
- `REUSE.toml` and `LICENSES/Apache-2.0.txt`; the repository passes
  `reuse lint` (REUSE Specification 3.3).
