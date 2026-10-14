# Changelog

All notable changes to this repository are documented here. The format is
loosely based on [Keep a Changelog](https://keepachangelog.com/).

## Unreleased

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
