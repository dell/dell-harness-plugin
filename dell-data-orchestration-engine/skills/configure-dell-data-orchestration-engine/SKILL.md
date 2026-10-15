---
name: configure-dell-data-orchestration-engine
description: >
  Configure, authenticate, validate, and troubleshoot the Dell Data
  Orchestration Engine (Dataloop) Python SDK MCP integration. Use for Dataloop
  API-key setup, bundled MCP runtime installation, MCP profile selection,
  connection validation, TLS settings, or tool-catalog and authorization problems. Do not use for DDAE or DDSE connection setup.
---

# Configure Dell Data Orchestration Engine

Use the Python MCP server bundled with this plugin. This release is a local
stdio integration built on the Dataloop `dtlpy` SDK; do not describe it as a
hosted MCP service or as OAuth enabled.

## Establish the supported runtime

1. Confirm Python 3.10 or later is available to the Codex process.
2. Install the dependencies from the bundled `mcp-server` package in that same
   Python environment.
3. Keep `dtlpy` state separate from any interactive `dlp` session and from
   every other Dataloop deployment by using a private `DATALOOP_PATH`.
4. Keep the MCP dependency on `mcp>=2.0,<2.1` until compatibility with 2.1 or
   later is implemented and tested in the bundled server.
5. Restart Codex after installation or configuration changes.

Read `references/installation-and-authentication.md` for exact setup and
credential-handling steps. Never paste an API key into chat, plugin metadata,
a repository, an MCP tool argument, or a support ticket.

## Authenticate

Use `scripts/configure_dataloop.py` to collect the key without echo and store it
in `~/.config/dell-mcp/dataloop.json` with mode 0600. Environment variables may
be used for a managed deployment and take precedence over the file.

The required values are:

- `DATALOOP_API_KEY`: a scoped Dataloop user API key.
- `DATALOOP_PATH`: a private directory used only by this MCP process.

Optional values are `DATALOOP_GATE_URL`, `DATALOOP_VERIFY_SSL`, and
`DATALOOP_MCP_PROFILES`. `DATALOOP_MCP_COMMAND` is an administrator-only
override. Leave TLS verification
enabled except in an explicitly approved private lab with a self-signed chain.

The SDK supports one identity per process. Use a separate MCP process and state
directory for a different identity or API gate. Use
`scripts/dataloop_state_dir.py` to bind a stable alias to one gate and refuse
accidental cross-deployment reuse. Do not switch identities inside a task.
OAuth is not implemented by this plugin release.

## Select only the needed tools

The launcher defaults to the reviewed `plugin-v1` surface: 42 tools used by the
eight evaluated workflows. Keep that default unless a deployment has a documented
need for another bounded profile. Available alternatives are:

- `discovery` for projects, datasets, items, and annotations.
- `pipelines` for pipeline graphs, lifecycle, and triggers.
- `models` for marketplace packages, models, embeddings, and PII.
- `rag` for direct RAG construction and pipeline support.

Combine profiles only when a workflow needs both. Use `all` only for deliberate
server development or contract testing; it is not the plugin default.
Read
`references/capability-catalog.md` for the current functional groups and known
limitations. Treat the live `tools/list` result as authoritative.

Read `references/evaluation-plan.md` before release validation or marketplace
submission. Use stable synthetic fixtures and retain the expected tool path,
confirmation exchange, transcript reference, and audit evidence.

## Validate safely

1. Run `configure_dataloop.py --check`; it must report a user-only file, an API
   key present, a state path, and a resolved bundled Python module.
   On POSIX, server startup repairs that state directory to 0700 and dtlpy's
   `cookie.json` to 0600; symbolic-link state paths are refused.
2. Start a new Codex session and inspect `tools/list`.
3. Call `project_list`; report only projects returned for the current identity.
4. Resolve one authorized project with `project_get`.
5. If the user requests a write test, use a disposable project, preview the
   exact target and effect, and obtain explicit approval before proceeding.

Do not use a production dataset for a smoke test. Do not broaden permissions to
make a test pass.

## Troubleshoot

- Missing tool server: confirm the bundled server is present and its
  dependencies are installed.
- Missing configuration: run the helper or set both required environment
  variables before Codex starts.
- Authentication failure: replace the key outside chat and confirm that its
  audience matches the configured API gate.
- HTML or unexpected response: use the Dataloop API gate, not the web console.
- Certificate failure: install the correct trust chain; do not disable
  verification in production.
- Empty project list: confirm the current key's Dataloop memberships rather than
  changing identity or requesting administrative access.
- Unexpected tools: verify `DATALOOP_MCP_PROFILES` and restart the client.
- Generic tool errors on MCP 2.1 or later: use the validated 2.0 line pending a
  bundled-server compatibility fix.

## Preserve boundaries

Dataloop authorization, project membership, dataset access, compute policy,
quotas, and audit records remain authoritative. Ask before any ambiguous write.
Require a concise plan and explicit approval for destructive, long-running,
billable, persistent, or code-executing actions. Never claim a workflow passed
without a retained test result from an authorized disposable project.
