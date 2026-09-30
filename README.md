# Dell Harness plugins

Product-oriented plugins that give Codex, and future supported agentic
harnesses, a consistent way to use Dell engineering services and governed
development workflows.

| Plugin | Integration | Authentication |
| --- | --- | --- |
| `dell-devspaces-sandbox` | Dell Redhat Devspaces AI Factory Skill; connector lifecycle and SSH | Interactive connector authentication |
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

**Dell Redhat Devspaces AI Factory Skill** uses the Devspaces CLI connector for
authentication guidance, workspace status, start/stop/restart, and managed SSH
connections. Its existing plugin ID remains `dell-devspaces-sandbox`.

Provide Python with `mcp` and set `DEVSPACES_CONNECTOR_COMMAND` to the approved
utility. Configure `DEVSPACES_CLUSTER_URL` (default:
`https://devspaces.example.com`) and the approved internal CA through
`NODE_EXTRA_CA_CERTS`. Authenticate interactively with the connector before use.

Lifecycle writes and deletion remain gated. See
[dell-devspaces-sandbox/README.md](dell-devspaces-sandbox/README.md) for setup,
connection lifetime, validation, and the optional legacy Kubernetes backend.

## Configure Dell data engines

The DDAE, DDSE, and Dataloop plugins contain no customer endpoint or credential.
Set the environment variables documented in each plugin README in the
environment that starts the harness. DDAE uses interactive OAuth. Self-managed
DDSE uses a dedicated, least-privilege API key through
`DDSE_MCP_AUTHORIZATION`. Dataloop uses a scoped API key consumed by its local
Python SDK MCP server.

## Governance

- `.agents/plugins/marketplace.json` is the Codex marketplace catalog.
- Harness-specific manifests and connection configuration stay inside the
  corresponding product directory.
- `requirements.toml` contains the enterprise MCP allowlist.
- The repository contains no PAT values or other credentials.
