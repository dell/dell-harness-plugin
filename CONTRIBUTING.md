# Contributing to Dell Harness Plugins

Thanks for your interest in contributing. This document covers the mechanics
of getting a change reviewed and merged. For what the plugins do, see the
[README](README.md) and each plugin's own README.

## TL;DR

1. Open an issue describing the bug or feature, unless the change is trivial.
2. Fork the repository and create a topic branch (`fix/…` or `feat/…`) from `main`.
3. Make your change with tests. Keep commits focused and self-explanatory.
4. Sign off every commit with `git commit -s` (see
   [Developer Certificate of Origin](#developer-certificate-of-origin)).
5. Push the branch to your fork and open a pull request against `main`.
6. CI must be green and one maintainer must approve before merge.

## Developer Certificate of Origin

Contributions are accepted under the terms of the
[Developer Certificate of Origin](https://developercertificate.org/) (DCO). To
signal your acceptance, sign off every commit with `--signoff` (`-s`):

```bash
git commit -s -m "fix(devspaces): reject wildcard namespaces"
```

This appends a `Signed-off-by: Your Name <you@example.com>` trailer using your
configured `user.name` and `user.email`. Commits without a valid sign-off are
not merged. No separate CLA is required.

## Repository layout

Each top-level plugin directory is one Dell product or service:

| Directory | Plugin |
| --- | --- |
| `dell-devspaces-sandbox/` | Dell Redhat Devspaces AI Factory Skill |
| `dell-data-analytics-engine/` | Dell Data Analytics Engine |
| `dell-data-search-engine/` | Dell Data Search Engine |
| `dell-data-orchestration-engine/` | Dell Data Orchestration Engine (Dataloop) |

Shared evaluation cases and fixtures live under `evals/`, repository-wide tests
under `tests/`, and the Codex marketplace catalog in
`.agents/plugins/marketplace.json`. New plugins need a maintainer-approved issue
before a pull request.

## Development and testing

Python 3.10 or newer is required. The DevSpaces and Data Orchestration Engine
packages pin different `mcp` releases, so test them in separate virtual
environments:

```bash
# Repository-wide packaging, evaluation, and release-hygiene tests
python -m pytest tests -q

# Dell Redhat Devspaces AI Factory Skill
python -m venv .venv-devspaces
.venv-devspaces/bin/pip install -r dell-devspaces-sandbox/requirements-dev.txt
.venv-devspaces/bin/python -m pytest dell-devspaces-sandbox/tests -q
.venv-devspaces/bin/python dell-devspaces-sandbox/scripts/validate_plugin.py

# Dell Data Orchestration Engine MCP server
python -m venv .venv-doe
.venv-doe/bin/pip install -e "dell-data-orchestration-engine/mcp-server[dev]"
(cd dell-data-orchestration-engine/mcp-server && ../../.venv-doe/bin/python -m pytest -q)
```

## Coding and content rules

- **No credentials.** Never commit tokens, API keys, cookies, kubeconfigs, or
  authentication logs, including in test fixtures or evaluation transcripts.
- **No private infrastructure.** Use reserved example domains
  (`example.com`, `example.test`, `example.invalid`) for every hostname, URL,
  cluster ID, and fixture. `tests/plugins/test_public_release_hygiene.py`
  rejects other hostnames that are not on its public allowlist.
- **Copyright header.** Every new source file (`.py`, `.ps1`, `.psm1`, `.cmd`,
  `.toml`) starts with the SPDX header, after any shebang line:

  ```python
  # SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
  # SPDX-License-Identifier: Apache-2.0
  ```

- **Write safety.** New write or destructive MCP tools must stay behind explicit
  opt-in gates and carry accurate MCP tool annotations.
- **Docs.** User-visible behavior changes need an update to the affected plugin
  README and a [CHANGELOG](CHANGELOG.md) entry under "Unreleased".

## Pull requests

Use a [Conventional Commits](https://www.conventionalcommits.org/) style title,
for example `feat(ddse): add bounded log-rate workflow`, and complete the
[pull request template](.github/pull_request_template.md). Describe what changed,
why, and how you verified it.

## Reporting security issues

Do not open public issues for vulnerabilities. Follow [SECURITY.md](SECURITY.md).

## Code of Conduct

Participation in this project is governed by the
[Code of Conduct](CODE_OF_CONDUCT.md).
