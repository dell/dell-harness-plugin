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

- `dell-data-orchestration-engine` 0.4.0: synchronized the bundled MCP server
  with the upstream DOE MCP server as of 2026-09-21. The `plugin-v1` profile
  grows from 36 to 42 tools (`item_get`, `annotation_get`, `pipeline_list`,
  `pipeline_pause`, `pipeline_delete`, `model_delete_subset`). Upstream also
  brings verified cleanup of temporary `rag_query` items and vectors
  (`query_cleanup`), allowlisted `pii_report` samples, project-ownership checks
  on lookups, and reuse of an identical existing pipeline. A new
  inspect-and-rollback evaluation workflow covers the added tools.

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
