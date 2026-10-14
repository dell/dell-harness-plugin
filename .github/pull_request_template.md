<!--
Thanks for contributing. Before you submit:

1. Every commit must be signed off with `git commit -s` (see CONTRIBUTING.md § DCO).
2. CI must be green, including tests/plugins/test_public_release_hygiene.py.
3. User-visible behavior changes need a README update and a CHANGELOG entry.
-->

## What does this PR do?

A one-paragraph summary. Which plugin (devspaces / ddae / ddse / doe / repository)? What behavior changes?

## Why?

Link the issue this closes (`Fixes #NNN`) or explain the motivation.

## How was this tested?

- [ ] Unit tests added or updated
- [ ] `python -m pytest tests -q` passes
- [ ] Affected plugin test suite passes (see CONTRIBUTING.md)
- [ ] Manually verified against a live service (describe how, without credentials or private hostnames)

## Breaking changes

- [ ] No breaking changes
- [ ] Breaking change to a tool name, input/output schema, or environment variable; CHANGELOG includes migration notes

## Checklist

- [ ] Commits are signed off (`git commit -s`)
- [ ] New source files carry the SPDX copyright header
- [ ] No credentials, tokens, customer data, or non-example hostnames added
- [ ] Write or destructive tools stay behind explicit opt-in gates
