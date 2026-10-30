# Security Policy

## Supported versions

The plugins are in early development (v0.x). Only the latest version on the
`main` branch receives security fixes.

## Reporting a vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**
Report privately so the maintainers can coordinate a fix and disclosure.

Use GitHub's **private vulnerability reporting** on this repository, or email
**security@dell.com**. Include:

- The affected plugin (`dell-ai-factory-agentic-workspace`, `dell-data-analytics-engine`,
  `dell-data-search-engine`, or `dell-data-orchestration-engine`) and version
  from its `.codex-plugin/plugin.json`.
- A clear description of the vulnerability and its impact.
- Reproduction steps, or enough detail to reproduce independently.
- A proof of concept and CVE identifier, if available.

Do not include real credentials, tokens, or customer data in a report.

## Response expectations

- **Acknowledgement:** within 3 business days.
- **Initial assessment:** within 7 business days.
- **Fix:** severity-dependent. Critical issues are prioritized for the next
  release; lower-severity issues may be batched.
- **Disclosure:** coordinated with the reporter once a fix is available.

Dell follows the
[Dell Vulnerability Response Policy](https://www.dell.com/support/kbdoc/en-us/000180119/dell-vulnerability-response-policy)
for CVE assignment and advisory publication.

## What counts as a security issue

- Credentials (API keys, OAuth tokens, SDK cookies, connector sessions) being
  exposed to the model, logs, chat transcripts, or other local users.
- A write or destructive MCP tool running without its documented opt-in gate
  or confirmation.
- TLS verification being disabled outside its documented, explicitly enabled
  scope.
- Path traversal or symlink attacks against plugin state or credential files.
- Bypass of namespace, project, or read-only restrictions enforced by a plugin.

General bugs, documentation errors, and vulnerabilities in the external Dell
or third-party services the plugins connect to should be reported through the
normal issue tracker or the respective product's support channel.
