# Release checklist

Use this checklist for every public plugin release. The release owner records the
completed checks in the pull request or release issue; do not store credentials,
private endpoints, or customer data in the evidence.

## Prepare

- [ ] Confirm the release owner and reviewer in `MAINTAINERS.md`.
- [ ] Update plugin versions, marketplace metadata, and `CHANGELOG.md` together.
- [ ] Document migrations for renamed plugins, tools, inputs, outputs, or
      environment variables.
- [ ] Confirm every new source file has the required SPDX header and every
      dependency change has compatible licensing.

## Validate

- [ ] Run `python scripts/validate_skills.py`.
- [ ] Run `python -m pytest tests -q`.
- [ ] Run the affected plugin test suites from `CONTRIBUTING.md`.
- [ ] Run REUSE compliance and gitleaks using the same versions as CI.
- [ ] Build a source archive from tracked files and run
      `python scripts/validate_public_archive.py <archive>`.
- [ ] Inspect the archive rather than relying only on the working tree.

## Publish

- [ ] Merge only after required reviews and green CI.
- [ ] Create signed plugin tags in the form `<plugin>/v<version>`.
- [ ] Publish release notes from the matching `CHANGELOG.md` section.
- [ ] Verify the public repository, raw README, tags, and installation commands
      while signed out of GitHub.

## After release

- [ ] Confirm each manifest and marketplace entry resolves to the released tag.
- [ ] Perform a clean installation and a credential-free startup check.
- [ ] Record follow-up issues for deferred live validation or documentation.
- [ ] Keep rollback instructions and the previous supported version available
      until the release is verified.
