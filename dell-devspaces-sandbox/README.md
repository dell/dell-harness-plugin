# Dell Redhat Devspaces AI Factory Skill

The default local MCP backend wraps the Devspaces CLI connector. It supports authentication guidance, list/status, start, stop, restart, managed SSH connect/disconnect, and separately gated deletion.

Version 0.2.0 changes the display name. Plugin ID/folder `dell-devspaces-sandbox` and skill invocation `$manage-devspaces-sandbox` stay stable for existing installations.

## Setup

The skill begins each new Dev Spaces workflow by asking for the user's credential-free HTTPS Dev Spaces browser URL, unless it was supplied in the initiating request. It derives the cluster origin, verifies that the plugin runtime is configured for that instance, and reuses existing authentication when valid. The example URL below is not a user-selection default. A connector-prompted Developer Workspace URI is separate and must be entered privately in the terminal.

Provide Python 3.10+ with the dependencies in `requirements.txt` and a Devspaces CLI connector executable obtained through your organization. Python 3.12 on Windows is the tested runtime. The executable is not redistributed here. The inspected Windows build reports `0.1.0 (168c6b8)`; verify `--help` when upgrading. Direct dependencies are pinned; this is not a complete transitive lockfile.

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

Use the CA file your organization approves for the cluster; omit NODE_EXTRA_CA_CERTS if the connector already trusts the server. The MCP backend ignores inherited NODE_TLS_REJECT_UNAUTHORIZED by default. An explicitly authorized certificate-failure retry can launch the plugin with DEVSPACES_ALLOW_INSECURE_TLS=true, which sets NODE_TLS_REJECT_UNAUTHORIZED=0 only on its connector children. Runtime reports tls_verification_enabled=false. This disables certificate verification and can permit interception; do not persist it in user/machine settings. See the skill troubleshooting reference for scoped authentication and restoration commands. The exception lasts for a connector tunnel's lifetime; restore normal trust on the next planned reconnect.

After the existing-session check shows authentication is needed, authenticate privately using the bundled Windows launcher (replace both example paths and the cluster origin):

```powershell
& 'C:\path\to\plugin\scripts\launch-auth.cmd' -ConnectorPath 'C:\Tools\Devspaces\connector.exe' -ClusterUrl 'https://your-cluster.example'
```

You can also open `scripts/launch-auth.cmd` from Explorer and supply the executable path and cluster origin at its local prompts. The launcher uses `-NoProfile -File` and an explicit completion pause; it does not require `-NoExit`, a PowerShell profile, or execution-policy changes. Complete the Developer Workspace URI prompt in the terminal, not chat. Return to Codex to verify authentication and list workspaces; neither process launch nor zero exit proves login. The MCP authentication tool returns instructions because this flow requires interactive input; it does not claim automatic browser SSO. If a programmatic launch has no visible window, stop only the owned auth helper and open the launcher manually, preserving existing tunnels. Add `-AllowInsecureTls` only for an explicitly authorized certificate-error retry.

Install from this marketplace using the existing plugin ID:

```powershell
codex plugin marketplace add https://github.com/dell/dell-harness-plugin.git
codex plugin add dell-devspaces-sandbox@dell-harness-plugin
```

The manifest launches `python`; ensure that interpreter has runtime dependencies before restarting Codex. Install `requirements.txt` for both backends, or `mcp==1.30.0` for the default connector backend. Installation does not provision the external connector or credentials.

If an already approved PowerShell 7 installation is preferred, set `$env:DEVSPACES_POWERSHELL = 'C:\path\to\pwsh.exe'` before running the launcher. This selects a host without changing its execution policy. The launcher defaults to Windows PowerShell. If that host rejects script execution, the `.cmd` wrapper keeps the error visible; use an administrator-approved execution method rather than bypassing policy. Synthetic helper tests can select the same host with `DEVSPACES_TEST_POWERSHELL` set to its absolute path; no real authentication is used by those tests.

## Operations

| Capability | Utility behavior |
| --- | --- |
| Authenticate | Interactive `--auth <clusterURL>`; instructions returned by MCP |
| List / status | Parse `--list` JSON; distinguish phase from desired started state |
| Start / stop | `--start <name>` / `--stop <name>`, then bounded verification |
| Restart | Stop, verify Stopped, start, verify Running; no native restart flag |
| Connect | Long-lived `--connect <name>`, tracked separately from workspace state |
| Disconnect | Terminate only the connector process owned by this MCP server |
| Delete | `--delete <name>`, both gates and exact name required; verify absence afterward |
| Create / dashboard / URL handler | Utility features outside the supported v0.2 plugin tool surface |

The adapter verifies clusterId, namespace, unique name, and the caller's expected workspace ID before remote actions. `list_devspaces` returns a page with `workspaces`, `total`, and `next_offset` (default 25, maximum 100). Mutations require `expected_workspace_id` from `devworkspaceId`. The instructions-only auth tool is `get_devspaces_authentication_instructions`. These are v0.2 API changes from the earlier draft. No personal workspace or localhost port is hard-coded. Names duplicated across namespaces are rejected because the utility accepts only a name. Stop/restart interrupt jobs; restarting requires reconnecting SSH.

Connections live with the MCP process. Graceful shutdown closes its tunnels; an abrupt process kill may require local cleanup. Ready means setup was reported and the process remains alive, not that Codex authenticated over SSH. For setup, execute the MCP connect workflow backed by an actual `--connect` invocation, then dispatch `codex://settings/connections/ssh/add?name=<encoded-alias>&enabled=true` for the observed alias. An existing SSH probe alone does not replace this setup step. The enabled flag is the requested default for this workflow; verify its effect in the app separately from URL dispatch. Preserve strict pinned host-key checks and existing tunnels until the fresh tunnel is verified. Manually launched connectors remain outside this server's control.

Lifecycle timeouts default to 120 seconds per transition, configurable from 1 to 300. A restart's stop timeout prevents start from being issued. Recheck state before retrying an uncertain mutation.

Client timeouts are separate: Codex documents a default MCP tool timeout of 60 seconds. Configure the installed client's effective server timeout to cover the chosen lifecycle waits and CLI command overhead, and verify it during installed-plugin acceptance. The current synchronous lifecycle implementation can outlast the default client timeout. A cancelled/timed-out client request is not proof that the server stopped working; inspect state before retrying. This remains an installed-client release check, not a passed test.

## Optional Kubernetes diagnostics

The original `scripts/devspaces_mcp_server.py` remains available for separately configured events/logs and legacy operations. It is not loaded by default. See [legacy setup](skills/manage-devspaces-sandbox/references/kubernetes-backend.md). It uses `config/instances.json`, kubeconfig, and `oc`; these do not configure the connector backend. Never silently switch backends on failure.

## Validation

```powershell
python -m pytest dell-devspaces-sandbox/tests -q
python dell-devspaces-sandbox/scripts/validate_plugin.py
python dell-devspaces-sandbox/scripts/connector_mcp_server.py --check
```

Install `requirements-dev.txt` for offline validation. The validator checks packaging, boots an extracted ZIP, runs unit and MCP protocol tests, and writes evidence under `validation/evidence`. The last command is a read-only live check requiring authentication and CA trust. Synthetic tests do not validate live lifecycle changes or an end-to-end Codex SSH session. See [review and release gates](validation/REVIEW.md) and [workflow evaluation cases](evals/cases.json).

## Scope and support

This is a local Codex plugin that runs on the user's workstation and talks to a Dev Spaces cluster that the user's organization operates. It is not a hosted connector, and ChatGPT web/mobile compatibility is out of scope. The Devspaces CLI connector is a separate executable that this repository does not distribute. Report issues through this repository's [issue tracker](https://github.com/dell/dell-harness-plugin/issues) and security vulnerabilities through [SECURITY.md](../SECURITY.md).
