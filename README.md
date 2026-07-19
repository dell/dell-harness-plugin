# Dell Harness Codex plugins

gateway.

| Plugin | Gateway | Credential variable |
| --- | --- | --- |

Live MCP `tools/list` is authoritative for the tools exposed by each gateway.
Write and destructive operations use Codex write approval mode.

## Install

```powershell
codex plugin marketplace add https://github.com/dell/dell-harness-plugin.git
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

## Governance

- `.agents/plugins/marketplace.json` is the Codex marketplace catalog.
- `requirements.toml` contains the enterprise MCP allowlist.
- The repository contains no PAT values or other credentials.
