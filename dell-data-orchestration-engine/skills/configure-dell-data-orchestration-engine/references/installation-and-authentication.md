# Installation and authentication

## Supported deployment for version 0.3.0

The plugin starts its bundled Python MCP server as a local `stdio` subprocess.
The server calls Dataloop through the `dtlpy` Python SDK. Each Codex runtime
starts its own process; there is no shared hosted MCP service in this release.

Install Python 3.10 or later and the bundled package dependencies in the runtime
used by Codex:

```bash
python --version
python -m pip install "./dell-data-orchestration-engine/mcp-server"
```

The server currently passes its full automated suite with `mcp>=2.0,<2.1`.
MCP 2.1 changed ordinary tool-exception behavior and breaks the existing test
contract, so treat a broader dependency range as unsupported until adapted and
revalidated.

## User setup

Run:

```bash
python dell-data-orchestration-engine/scripts/configure_dataloop.py
python dell-data-orchestration-engine/scripts/configure_dataloop.py --check
```

The helper asks for:

- a Dataloop API key, entered without terminal echo;
- an optional self-hosted API gate URL;
- an optional MCP profile selection (the default is `plugin-v1`);
- an optional administrator-approved external executable override.

On POSIX systems it writes `~/.config/dell-mcp/dataloop.json` with mode 0600
and creates a mode 0700 SDK state directory. Server startup repairs the state
directory to mode 0700 and dtlpy's `cookie.json` to mode 0600, including state
left by an older release. Symbolic-link state paths are refused. On Windows,
store the file in the user profile and apply a user-only ACL. Do not copy it to
a repository or shared filesystem.

For multiple Dataloop gates, create one state directory and configuration file
per deployment:

```bash
STATE_PATH="$(python dell-data-orchestration-engine/scripts/dataloop_state_dir.py cto-lab \
  --gate-url https://gate.example.com/api/v1)"
python dell-data-orchestration-engine/scripts/configure_dataloop.py \
  --config ~/.config/dell-mcp/dataloop-cto-lab.json \
  --state-path "$STATE_PATH" \
  --gate-url https://gate.example.com/api/v1
```

Reusing an alias with another gate URL fails instead of mixing cookies and the
SDK's currently selected deployment.

For managed deployment, provide the equivalent settings through a secret
manager or protected process environment. Environment variables override the
file:

| Variable | Required | Purpose |
| --- | --- | --- |
| `DATALOOP_API_KEY` | yes | Scoped Dataloop user identity |
| `DATALOOP_PATH` | yes | Private dtlpy state directory |
| `DATALOOP_GATE_URL` | no | Self-hosted API gate, not the console URL |
| `DATALOOP_VERIFY_SSL` | no | TLS verification, default `true` |
| `DATALOOP_MCP_PROFILES` | no | `plugin-v1` by default; bounded profiles or explicit `all` |
| `DATALOOP_MCP_COMMAND` | no | Administrator-approved external executable override; bundled server is the default |
| `DATALOOP_CONFIG_FILE` | no | Alternate protected JSON config |

## Administrator responsibilities

Provide:

- the approved Dataloop API gate;
- the process for issuing and revoking API keys;
- a least-privilege role mapped to the required projects and operations;
- compute quota and approval expectations for training, deployment, pipelines,
  embeddings, and triggers;
- retention, annotation, PII, and audit policy;
- a disposable project and synthetic datasets for release evaluation.

Do not issue a shared administrator key for normal use. Rotate and revoke keys
through Dataloop, not through the plugin.

## Validate

After restarting Codex:

1. Confirm `dell_dataloop` initializes and inspect `tools/list`.
2. Call `project_list`; compare the result with the expected user membership.
3. Resolve one authorized project with `project_get`.
4. Run read-only workflow cases against synthetic data.
5. Run write, cost, persistent-resource, and destructive cases only in the
   disposable project with explicit approval and retained audit evidence.
