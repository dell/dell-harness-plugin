# Dell Harness plugins

Product-oriented plugins that give Codex, and future supported agentic
harnesses, a consistent way to use Dell engineering services and governed
development workflows.

| Plugin | Integration | Authentication |
| --- | --- | --- |
| `dell-devspaces-sandbox` | Local, namespace-scoped OpenShift DevWorkspace MCP adapter | Automatically initiated OpenShift SSO |
| `dell-data-analytics-engine` | Customer-managed DDAE/Starburst MCP | Interactive OAuth |
| `dell-data-search-engine` | Customer-managed DDSE Kibana Agent Builder MCP | Least-privilege API key |
| `dell-data-orchestration-engine` | Local Dataloop Python SDK MCP server | Scoped Dataloop API key |

Live MCP `tools/list` is authoritative for the tools exposed by each gateway.
Write and destructive operations use Codex write approval mode.

## Install with Codex

```powershell
codex plugin marketplace add https://github.com/dell/dell-harness-plugin.git
codex plugin add dell-devspaces-sandbox@dell-harness-plugin
codex plugin add dell-data-analytics-engine@dell-harness-plugin
codex plugin add dell-data-search-engine@dell-harness-plugin
codex plugin add dell-data-orchestration-engine@dell-harness-plugin
```

does not execute a script or prompt for a PAT.

## Repository organization

Each top-level plugin directory represents one Dell product or service. Shared
workflow skills, documentation, evaluations, and support expectations remain
in that product directory. Harness-specific connection files are adapters
inside the same directory:

- Codex uses `.codex-plugin/plugin.json` and `.mcp.json`.
- Devin uses `.devin-plugin/plugin.json` and `mcp_config.json` when that adapter
  has been implemented and validated.

Not every product supports every harness yet. The manifests present in each
product directory are the authoritative statement of current support.

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

The Dev Spaces plugin runs its MCP server locally through stdio. Its skill
selects a logical instance from the managed catalog, checks authentication,
and automatically starts `oc login --web` when SSO is missing or expired.
The user only completes the corporate browser challenge.

The managed runtime must include `python`, the pinned Python dependencies,
and `oc`. Administrators maintain instance API/UI URLs centrally in
`dell-devspaces-sandbox/config/instances.json` or through
`DEVSPACES_INSTANCES_FILE`; users do not enter URLs or tokens.

Lifecycle writes and deletion are disabled by default. See
`dell-devspaces-sandbox/README.md` for the security model, dependencies, and
validation commands.

## Configure Dell data engines

The DDAE, DDSE, and Dataloop plugins contain no customer endpoint or credential.
Set the environment variables documented in each plugin README in the
environment that starts the harness. DDAE uses interactive OAuth. Self-managed
DDSE uses a dedicated, least-privilege API key through
`DDSE_MCP_AUTHORIZATION`. Dataloop uses a scoped API key consumed by its local
Python SDK MCP server.

For a private deployment reachable only from Dev Spaces or a bastion-connected
host, follow [`MCP_PRIVATE_NETWORK_TESTING.md`](MCP_PRIVATE_NETWORK_TESTING.md).

## Governance

- `.agents/plugins/marketplace.json` is the Codex marketplace catalog.
- Harness-specific manifests and connection configuration stay inside the
  corresponding product directory.
- `requirements.toml` contains the enterprise MCP allowlist.
- The repository contains no PAT values or other credentials.
