# Dell Data Orchestration Engine plugin

This plugin connects Codex to Dell Data Orchestration Engine (Dataloop) through
a bundled Python MCP server built on the Dataloop `dtlpy` SDK. It provides
outcome-focused skills for dataset exploration, agentic RAG, PII handling, and
image classification.

The Dataloop account remains the authorization boundary. The plugin does not
elevate permissions, bypass Dataloop controls, or run pipeline code locally.

## Runtime prerequisites

- Python 3.10 or later
- the bundled server Python dependencies installed in the environment used by Codex
- a Dataloop API key for a non-administrative user
- a private `DATALOOP_PATH` directory for the SDK state

From a repository checkout, install the bundled server and its constrained
runtime dependencies:

```bash
python -m pip install "./dell-data-orchestration-engine/mcp-server"
```

The launcher executes the source under `mcp-server/src`; it does not clone or
run code from another repository. The currently validated dependency
combination uses `mcp>=2.0,<2.1`.

## Configure authentication

Run the plugin helper. It prompts for the API key without echoing it and writes
a user-only configuration file:

```bash
python dell-data-orchestration-engine/scripts/configure_dataloop.py
python dell-data-orchestration-engine/scripts/configure_dataloop.py --check
```

The default file is `~/.config/dell-mcp/dataloop.json`. Set
`DATALOOP_CONFIG_FILE` to use an enterprise-managed path. Environment variables
with the same names take precedence over the file.

Restart Codex after changing the configuration. Start with `project_list` to
verify the effective identity and authorization boundary.

## Limit the tool surface

The launcher defaults to the reviewed `plugin-v1` profile: the 36 tools used by
the plugin's seven evaluated workflows. This keeps tool-selection context bounded
while retaining the complete bundled server for deliberate development use.

Set `DATALOOP_MCP_PROFILES` to a comma-separated selection of `discovery`,
`pipelines`, `models`, and `rag` when a deployment needs a different bounded
surface. For example:

```bash
export DATALOOP_MCP_PROFILES=discovery,models
```

Set the value to `all` only when the complete 70-tool server is deliberately
required. Prefer the default or the smallest profile set that supports the work.

## Security defaults

- No credentials are stored in the plugin manifest or MCP request payloads.
- On POSIX systems, the launcher refuses group-readable or world-readable
  configuration and SDK-state paths.
- On Windows, keep the configuration in the user's profile and apply a
  user-only ACL through the managed deployment.
- State-changing, destructive, long-running, and billable actions require a
  user-visible plan and explicit approval. Critical operations also enforce a
  confirmation field or elicitation at the server boundary.
- Project IDs, dataset IDs, model IDs, and pipeline IDs are resolved before a
  write. Names are not treated as authorization.
- PII workflows return types and counts, not raw detected values.
- TLS verification defaults to enabled. Disabling it is limited to a private
  lab environment and requires an explicit setting.

## Authentication boundary

The Python SDK integration uses a Dataloop API key and one identity per server
process. OAuth is not implemented in the current release and must not be
represented as available.

## Roadmap

The current release is a local `stdio` MCP integration. Roadmap items are:

- a centrally hosted MCP service for managed, shared operation;
- a supported Dataloop CLI integration that agentic harnesses can invoke;
- OAuth-based user authentication and delegated authorization.

These items require product design, security review, implementation, and
validation before they can be documented as supported capabilities.

## Validate

```bash
python -m pytest tests/plugins -q
python scripts/aidp_eval.py validate
python dell-data-orchestration-engine/scripts/export_dataloop_contract.py > /tmp/dataloop-tools.json
python scripts/aidp_eval.py contract dell-data-orchestration-engine /tmp/dataloop-tools.json --review-ready
```

Live evaluation requires a disposable Dataloop project and an intentionally
scoped test identity. Never run write-path tests against customer production
data.
