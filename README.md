# Dell Harness Codex plugins

Codex plugins for Dell engineering services and governed development
workflows.

| Plugin | Integration | Authentication |
| --- | --- | --- |
| `dell-devspaces-sandbox` | Local, namespace-scoped OpenShift DevWorkspace MCP adapter | Existing kubeconfig/OpenShift OAuth identity |

Live MCP `tools/list` is authoritative for the tools exposed by each gateway.
Write and destructive operations use Codex write approval mode.

## Install

```powershell
codex plugin marketplace add https://github.com/dell/dell-harness-plugin.git
codex plugin add dell-devspaces-sandbox@dell-harness-plugin
```

does not execute a script or prompt for a PAT.

## Authorize on Windows

Clone this repository, open Windows Terminal at its root, and run either
Windows PowerShell 5.1:

```powershell
```

or PowerShell 7:

```powershell
```

The prompt is hidden. Never paste a PAT into chat, a command line, config JSON,
or the repository. Each PAT is plaintext at rest in the active
`%CODEX_HOME%\.env`, defaulting to `%USERPROFILE%\.codex\.env`, with a Windows
ACL restricted to the current user and SYSTEM.

After enrollment, fully quit ChatGPT/Codex Desktop from the system tray,
restart it, and start a new task. See each plugin README for status, rotation,
removal, and troubleshooting commands.

## Configure Dev Spaces

The Dev Spaces plugin runs its MCP server locally through stdio and connects
to the selected OpenShift API with the user's existing kubeconfig identity.
Before starting Codex, pin the intended cluster and authorized namespaces:

```powershell
$env:DEVSPACES_CONTEXT = "my-openshift-context"
$env:DEVSPACES_ALLOWED_CONTEXTS = "my-openshift-context"
$env:DEVSPACES_ALLOWED_NAMESPACES = "my-user-devspaces"
```

Lifecycle writes and deletion are disabled by default. See
`dell-devspaces-sandbox/README.md` for the security model, dependencies, and
validation commands.

## Governance

- `.agents/plugins/marketplace.json` is the Codex marketplace catalog.
- `requirements.toml` contains the enterprise MCP allowlist.
- The repository contains no PAT values or other credentials.
