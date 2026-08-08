# Dell Harness Codex plugins

Codex plugins for Dell engineering services and governed development
workflows.

| Plugin | Integration | Authentication |
| --- | --- | --- |
| `dell-devspaces-sandbox` | Local, namespace-scoped OpenShift DevWorkspace MCP adapter | Automatically initiated OpenShift SSO |
| `dell-data-analytics-engine` | Customer-managed DDAE/Starburst MCP | Interactive OAuth |
| `dell-data-search-engine` | Customer-managed DDSE Kibana Agent Builder MCP | Least-privilege API key |

Live MCP `tools/list` is authoritative for the tools exposed by each gateway.
Write and destructive operations use Codex write approval mode.

## Install

```powershell
codex plugin marketplace add https://github.com/dell/dell-harness-plugin.git
codex plugin add dell-devspaces-sandbox@dell-harness-plugin
codex plugin add dell-data-analytics-engine@dell-harness-plugin
codex plugin add dell-data-search-engine@dell-harness-plugin
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

The DDAE and DDSE plugins contain no customer endpoint or credential. Set the
environment variables documented in each plugin README in the environment that
starts Codex. DDAE uses interactive OAuth. Self-managed DDSE uses a dedicated,
least-privilege API key through `DDSE_MCP_AUTHORIZATION`.

For a private deployment reachable only from Dev Spaces or a bastion-connected
host, follow [`MCP_PRIVATE_NETWORK_TESTING.md`](MCP_PRIVATE_NETWORK_TESTING.md).

## Governance

- `.agents/plugins/marketplace.json` is the Codex marketplace catalog.
- `requirements.toml` contains the enterprise MCP allowlist.
- The repository contains no PAT values or other credentials.
