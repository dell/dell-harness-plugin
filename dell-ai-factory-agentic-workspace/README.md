# Dell AI Factory Agentic Workspace

The default local MCP backend wraps the Devspaces CLI connector. It supports authentication guidance, list/status, start, stop, restart, managed SSH connect/disconnect, and separately gated deletion.

The plugin prepares its Python runtime automatically and exposes native authentication launch/progress tools for uninterrupted setup. Invoke its skill as `$manage-agentic-workspace`.

Version 0.5.0 renames the plugin ID and folder from `dell-devspaces-sandbox` to `dell-ai-factory-agentic-workspace` and the skill from `$manage-devspaces-sandbox` to `$manage-agentic-workspace`. Existing installations do not update automatically: run `codex plugin remove dell-devspaces-sandbox@dell-harness-plugin` first (both IDs register the same `dell_devspaces` MCP server), then install the new ID as shown below and reapply any local MCP argument changes such as the legacy backend selection. MCP server name `dell_devspaces`, tool names, environment variables, and the runtime cache location are unchanged.

## Setup

The skill begins each new Dev Spaces workflow by asking for the user's credential-free HTTPS Dev Spaces browser URL, unless it was supplied in the initiating request. It derives the cluster origin, verifies that the plugin runtime is configured for that instance, and reuses existing authentication when valid. The example URL below is not a user-selection default. A connector-prompted Developer Workspace URI is separate and must be entered privately in the terminal.

Provide Python 3.11+ with the dependencies in `requirements.txt` and a Devspaces CLI connector executable obtained through your organization. Python 3.12 on Windows is the tested runtime. The executable is not redistributed here. The inspected Windows build reports `0.1.0 (168c6b8)`; verify `--help` when upgrading. Direct dependencies are pinned; this is not a complete transitive lockfile.

Set these variables in the environment that launches Codex. `DEVSPACES_CLUSTER_URL` is required and has no built-in default; until it is set, every remote tool fails closed without invoking the connector.

```powershell
$env:DEVSPACES_CONNECTOR_COMMAND = 'C:/Tools/Devspaces/devspaces-cli-connector-win-x64.exe'
$env:DEVSPACES_CLUSTER_URL = 'https://devspaces.example.com'
$env:NODE_EXTRA_CA_CERTS = 'C:/Certificates/your-organization-ca.pem'
# Optional namespace restriction:
$env:DEVSPACES_ALLOWED_NAMESPACES = 'your-devspaces-namespace'
# Enable for intended lifecycle use:
$env:DEVSPACES_ENABLE_WRITES = 'true'
# Deletion stays disabled unless separately enabled:
# $env:DEVSPACES_ENABLE_DELETE = 'true'
```

Certificate verification is always on. A certificate failure during listing, sign-in, a lifecycle action, or a tunnel launch is reported as a failure and is never retried with verification disabled; a failed or cancelled sign-in cannot change TLS settings for later calls. Point `NODE_EXTRA_CA_CERTS` at your organization's CA bundle. For an isolated lab cluster only, an administrator can set `DEVSPACES_ALLOW_INSECURE_TLS=true` in the environment that launches Codex. The server reads it once at startup, reports it as `tls_verification_enabled=false`, and nothing at runtime turns it on or off. `DEVSPACES_AUTO_TLS_RETRY` is no longer read (removed in 0.6.0), and ambient `NODE_TLS_REJECT_UNAUTHORIZED` is ignored. See [troubleshooting](skills/manage-agentic-workspace/references/troubleshooting.md).

A configure/connect request includes beginning native login when CLI access is unverified; no second user request is needed. The agent monitors structured authentication progress itself, then checks CLI access. It does not ask the user to validate terminal text. An empty inventory is inconclusive; finish native authentication before asking about account or workspace assignment. The agent runs local commands and diagnostics itself. User participation is limited to SSO/MFA, credentials, and private terminal input. Authentication uses the machine's default browser through the native CLI; workspace status and validation always use the CLI, not dashboard browsing. Browser sign-in alone never proves CLI access.

Bundled helpers are part of the plugin:

| Helper | Purpose |
| --- | --- |
| `scripts/authenticate_devspaces.py` | Standard-library-only native console launcher, CLI preflight, interactive auth, post-login verification; certificate failures fail closed; `--check` never starts login |
| `scripts/authenticate_devspaces.ps1` | Repeatable PowerShell wrapper around the same Python workflow |
| `scripts/launch-auth.cmd` | Explorer/native-terminal launcher that calls Python directly, including when `.ps1` execution is blocked |
| `scripts/prepare_runtime.py` | Reusable isolated Python/MCP runtime preparation and server launcher |
| `scripts/mcp_session.py` | Retained MCP client fallback, eliminating generated per-chat clients |
| `scripts/connector_mcp_server.py --check` | Read-only runtime and workspace diagnosis; requires the MCP dependencies |

Run the Python launcher with the configured interpreter (the agent performs this, using actual configured paths):

```powershell
& 'C:\Python312\python.exe' 'C:\path\to\plugin\scripts\authenticate_devspaces.py' --connector 'C:\Tools\Devspaces\connector.exe' --cluster 'https://devspaces.example.com'
```

It opens a native Windows console and lets the CLI use the machine browser. Add `--foreground --pause` to use an existing local terminal. It skips login when CLI access to the cluster is already verified, and verifies access after auth; an empty list is inconclusive. Use `--check` for read-only CLI validation without starting login. Never run interactive foreground auth through a tool that captures the terminal stream. The Python helper does not depend on MCP or change PowerShell execution policy. On non-Windows systems use `--foreground` in a local terminal. The launcher reports only process launch, never authenticated status from a PID.

If SSO has completed in the machine browser and the terminal remains at `Please enter the Developer Workspace URI:`, you may press **Enter once** with no URI to let the attempt finish. An empty entry can cancel the attempt on some versions; the agent must recheck CLI access afterward before reporting success. See the [prompt guidance](skills/manage-agentic-workspace/references/troubleshooting.md#developer-workspace-uri-prompt-after-browser-sso). Never share a credential-bearing URI in chat.

Optional PowerShell entry point:

```powershell
& 'C:\path\to\plugin\scripts\authenticate_devspaces.ps1' -ConnectorPath 'C:\Tools\Devspaces\connector.exe' -ClusterUrl 'https://devspaces.example.com' -PythonPath 'C:\Python312\python.exe' -PauseOnCompletion
```

When `.ps1` files are blocked, invoke Python directly or run `launch-auth.cmd --connector <path> --cluster <origin>`; batch arguments now use Python-style switches. Set `DEVSPACES_PYTHON_COMMAND` for the batch launcher's interpreter, or use the Python launcher's explicit interpreter. Never paste batch source into PowerShell or change execution policy to make a helper run. Complete private authentication prompts only in the terminal. The agent checks actual native-window state and CLI access before reporting success.

Install from this marketplace:

```powershell
codex plugin marketplace add https://github.com/dell/dell-harness-plugin.git
codex plugin add dell-ai-factory-agentic-workspace@dell-harness-plugin
```

The manifest launches `python` with `scripts/prepare_runtime.py`. The bootstrap verifies MCP stdio imports and the pinned MCP version, reuses a working interpreter, or prepares a reusable isolated venv outside the plugin cache. `DEVSPACES_RUNTIME_DIR` can select the cache parent. Package installation uses normal verified TLS and writes logs only to stderr, preserving MCP stdout. If the `python` command is unavailable, resolve an installed interpreter and configure its absolute path. Run `prepare_runtime.py --prepare-only` for installation diagnostics or before restarting a client with a short server-startup timeout. Install `requirements.txt` for both backends, or `mcp==1.30.0` for the default connector backend. Installation does not provision the external connector or credentials.


## Desktop package

`python scripts/build_plugin_bundle.py dell-ai-factory-agentic-workspace`
builds self-contained ZIPs for Windows x64 and Linux x86_64 with a pinned
CPython 3.12.16 and the pinned MCP dependencies. The packaged `mcp.json` starts
`./runtime/...`, so no system Python or first-start installation is needed, and
the bootstrap never installs at runtime. The connector executable is still
supplied by your organization.

## SSH configuration ownership

The CLI owns `devspaces.conf`, its `Include` entries, and generated SSH settings.
The plugin does not read, write, or migrate any SSH configuration or known-hosts
file. Existing host-key pins, including legacy `Dell Devspaces pinned trust`
blocks written by 0.4.0, stay exactly as they are through successful, failed,
and repeated connects. Since 0.6.0 the plugin no longer removes those legacy
blocks; delete one by hand if you no longer want it.

The inspected CLI writes `StrictHostKeyChecking no` and `UserKnownHostsFile nul`
on Windows. Honoring that policy skips server identity verification. The plugin
does not claim verified server identity from a successful SSH login under that
policy. Organization-managed SSH policies still apply.

Workspace Running, tunnel readiness, SSH login, and Codex connection are separate
checks. If a workspace or tunnel exits, reconnect through `connect_devspace`;
the CLI refreshes the local port. Keep the owning MCP session alive.

## Operations

| Capability | Utility behavior |
| --- | --- |
| Authenticate | `begin_devspaces_authentication` launches native SSO; `get_devspaces_authentication_progress` reports sanitized completion and never changes TLS verification |
| List / status | Parse `--list` JSON; distinguish phase from desired started state |
| Start / stop | `--start <name>` / `--stop <name>`, then bounded verification |
| Restart | Stop, verify Stopped, start, verify Running; no native restart flag |
| Connect | Long-lived `--connect <name>`, tracked separately from workspace state |
| Disconnect | Terminate only the connector process owned by this MCP server |
| Delete | `--delete <name>`, both gates and exact name required; verify absence afterward |
| Create / dashboard / URL handler | Utility features outside the supported plugin tool surface |

The adapter verifies clusterId, namespace, unique name, and the caller's expected workspace ID before remote actions. `list_devspaces` returns a page with `workspaces`, `total`, and `next_offset` (default 25, maximum 100). Mutations require `expected_workspace_id` from `devworkspaceId`. The instructions-only fallback tool remains `get_devspaces_authentication_instructions`; the two new launch/progress tools handle setup directly. These API changes date from 0.2.0. No personal workspace or localhost port is hard-coded. Names duplicated across namespaces are rejected because the utility accepts only a name. Stop/restart interrupt jobs; restarting requires reconnecting SSH.

Connections live with the MCP process. Graceful shutdown closes its tunnels; an abrupt process kill may require local cleanup. Ready means setup was reported and the process remains alive, not that Codex authenticated over SSH. For setup, execute the MCP connect workflow backed by an actual `--connect` invocation, then dispatch `codex://settings/connections/ssh/add?name=<encoded-alias>&enabled=true` for the observed alias. An existing SSH probe alone does not replace this setup step. The enabled flag is the requested default for this workflow; verify its effect in the app separately from URL dispatch. Leave SSH configuration and host-key policy to the CLI; preserve existing tunnels until the fresh tunnel is verified. Manually launched connectors remain outside this server's control.

Lifecycle timeouts default to 120 seconds per transition, configurable from 1 to 300. A restart's stop timeout prevents start from being issued. Recheck state before retrying an uncertain mutation.

Client timeouts are separate: Codex documents a default MCP tool timeout of 60 seconds. Configure the installed client's effective server timeout to cover the chosen lifecycle waits and CLI command overhead, and verify it during installed-plugin acceptance. The current synchronous lifecycle implementation can outlast the default client timeout. A cancelled/timed-out client request is not proof that the server stopped working; inspect state before retrying. This remains an installed-client release check, not a passed test.

## Optional Kubernetes diagnostics

The original `scripts/devspaces_mcp_server.py` remains available for separately configured events/logs and legacy operations. It is not loaded by default. See [legacy setup](skills/manage-agentic-workspace/references/kubernetes-backend.md). It uses `config/instances.json`, kubeconfig, and `oc`; these do not configure the connector backend. Never silently switch backends on failure.

## Validation

```powershell
python -m pytest dell-ai-factory-agentic-workspace/tests -q
python dell-ai-factory-agentic-workspace/scripts/validate_plugin.py
python dell-ai-factory-agentic-workspace/scripts/connector_mcp_server.py --check
```

Install `requirements-dev.txt` for offline validation. The validator checks packaging, boots an extracted ZIP, runs unit and MCP protocol tests, and writes evidence under `validation/evidence`. The last command is a read-only live check requiring authentication and CA trust. Synthetic tests do not validate live lifecycle changes or an end-to-end Codex SSH session. See [review and release gates](validation/REVIEW.md) and [workflow evaluation cases](evals/cases.json).

## Scope and support

This is a local Codex plugin that runs on the user's workstation and talks to a Dev Spaces cluster that the user's organization operates. It is not a hosted connector, and ChatGPT web/mobile compatibility is out of scope. The Devspaces CLI connector is a separate executable that this repository does not distribute. Report issues through this repository's [issue tracker](https://github.com/dell/dell-harness-plugin/issues) and security vulnerabilities through [SECURITY.md](../SECURITY.md).

Detailed [setup scenarios and recovery](skills/manage-agentic-workspace/references/setup-scenarios.md) cover runtime, login, TLS, private prompts, duplicate windows, empty inventory, tunnels, and SSH trust.
