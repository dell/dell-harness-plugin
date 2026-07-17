# dell-harness-plugin

Dell Codex plugins for the Dell Pilot Workspace. Each plugin connects Codex
to a Dell internal service via the Dell MCP HTTP gateway.

## Plugins

| Plugin | Service | Instance | Gateway |
|--------|---------|----------|---------|

Each plugin provides 23 MCP tools for searching, reading, creating, and
managing pages, comments, labels, attachments, and page history.

## Install

```bash
```

target instance. The plugin ships no secrets.

## Governance

- `marketplace.json` -- plugin catalog for the Dell ISG marketplace
- `requirements.toml` -- MCP server allowlist for enterprise governance

## Security

- No tokens, passwords, or secrets are stored in this repository.
- All credentials use `${env:...}` placeholder syntax in `.mcp.json` files.
- PATs are stored per-user by Codex and never committed.
