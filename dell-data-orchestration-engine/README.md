# Dell Data Orchestration Engine plugin

This plugin connects Codex to Dell Data Orchestration Engine (Dataloop) through
a bundled Python MCP server built on the Dataloop `dtlpy` SDK. It provides
outcome-focused skills for dataset exploration, agentic RAG, PII handling, and
image classification.

The Dataloop account remains the authorization boundary. The plugin does not
elevate permissions, bypass Dataloop controls, or run pipeline code locally.

## Runtime prerequisites

- Python 3.11 or later with the standard `venv` module
- package-index access on first start (or the bundled server dependencies
  already installed in the interpreter that starts the plugin)
- a Dataloop API key for a non-administrative user
- a private `DATALOOP_PATH` directory for the SDK state

The launcher reuses the starting interpreter when it can already import the
bundled server. Otherwise it prepares a reusable isolated virtual environment
outside the plugin cache (under `%LOCALAPPDATA%` or `~/.cache`, in
`Dell/Dataloop/runtimes`; override with `DATALOOP_RUNTIME_DIR`) and installs the
dependencies declared in `mcp-server/pyproject.toml`. This keeps the `mcp` 2.0
line separate from other plugins, such as Agentic Workspace, that pin a
different `mcp` release in the same `python`. Installation output goes to
stderr, so MCP stdout stays clean.

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

For more than one Dataloop deployment, provision a different SDK state
directory for each gate and pass it to the configuration helper:

```bash
STATE_PATH="$(python dell-data-orchestration-engine/scripts/dataloop_state_dir.py private-lab \
  --gate-url https://gate.example.com/api/v1)"
python dell-data-orchestration-engine/scripts/configure_dataloop.py \
  --config ~/.config/dell-mcp/dataloop-private-lab.json \
  --state-path "$STATE_PATH" \
  --gate-url https://gate.example.com/api/v1
```

The alias is bound to its first recorded gate URL, preventing two deployments
from silently sharing one dtlpy cookie and selected-environment pointer.

## Limit the tool surface

The launcher defaults to the reviewed `plugin-v1` profile: the 42 tools used by
the plugin's eight evaluated workflows. This keeps tool-selection context bounded
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
  configuration, repairs SDK-state directories to mode 0700, and repairs
  dtlpy's bearer-token cookie to mode 0600.
- Symbolic-link SDK-state directories are refused.
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
python scripts/aidp_eval.py fixture dell-data-orchestration-engine
python dell-data-orchestration-engine/scripts/export_dataloop_contract.py > /tmp/dataloop-tools.json
python scripts/aidp_eval.py contract dell-data-orchestration-engine /tmp/dataloop-tools.json --review-ready
```

Live evaluation requires a disposable Dataloop project and an intentionally
scoped test identity. Never run write-path tests against customer production
data.

The latest package-level readiness assessment is recorded in
`evals/results/doe-initial-review-20260911/REVIEW.md`. It does not represent a
completed live Dataloop or ChatGPT review.
