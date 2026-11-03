# Dell Harness plugins

Product-oriented plugins that give Codex, and future supported agentic
harnesses, a consistent way to use Dell engineering services and governed
development workflows.

| Plugin | Integration | Authentication |
| --- | --- | --- |
| `dell-ai-factory-agentic-workspace` | Dell AI Factory Agentic Workspace; connector lifecycle and SSH | Interactive connector authentication |
| `dell-data-analytics-engine` | Customer-managed DDAE/Starburst MCP | Interactive OAuth |
| `dell-data-search-engine` | Customer-managed DDSE Kibana Agent Builder MCP | Least-privilege API key |
| `dell-data-orchestration-engine` | Local Dataloop Python SDK MCP server | Scoped Dataloop API key |

Live MCP `tools/list` is authoritative for the tools exposed by each gateway.
Write and destructive operations use Codex write approval mode.

## Install with Codex

```powershell
codex plugin marketplace add https://github.com/dell/dell-harness-plugin.git
codex plugin add dell-ai-factory-agentic-workspace@dell-harness-plugin
codex plugin add dell-data-analytics-engine@dell-harness-plugin
codex plugin add dell-data-search-engine@dell-harness-plugin
codex plugin add dell-data-orchestration-engine@dell-harness-plugin
```

See each plugin README for its runtime prerequisites and authentication steps.

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

## Configure Dev Spaces

**Dell AI Factory Agentic Workspace** uses the Devspaces CLI connector for
authentication guidance, workspace status, start/stop/restart, and managed SSH
connections. Its plugin ID is `dell-ai-factory-agentic-workspace` (formerly
`dell-devspaces-sandbox`). To upgrade, run
`codex plugin remove dell-devspaces-sandbox@dell-harness-plugin`, then install
the new ID.

Provide Python with `mcp` and set `DEVSPACES_CONNECTOR_COMMAND` to the
connector executable. Set `DEVSPACES_CLUSTER_URL` to your Dev Spaces HTTPS
origin (required; there is no default) and, if the cluster uses a private
certificate authority, point `NODE_EXTRA_CA_CERTS` at that CA bundle.
Authenticate interactively with the connector before use.

Lifecycle writes and deletion remain gated. See
[dell-ai-factory-agentic-workspace/README.md](dell-ai-factory-agentic-workspace/README.md) for setup,
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
- `requirements.toml` is an example Codex MCP allowlist for these plugins.
- The repository contains no PAT values or other credentials, and examples use
  reserved `example.com` / `example.test` hostnames.

## Contributing

Contributions are welcome. See the [contributors](CONTRIBUTORS.md), and read
[CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request; every commit
must be signed off under the Developer Certificate of Origin (`git commit -s`).
Participation is governed by the [Code of Conduct](CODE_OF_CONDUCT.md).

## Security

Do not report security vulnerabilities through public GitHub issues. Follow
[SECURITY.md](SECURITY.md).

## License

Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.

Licensed under the [Apache License, Version 2.0](LICENSE). See [NOTICE](NOTICE)
for attribution. Dell, Dell Technologies, and the Dell Technologies wordmark are
trademarks of Dell Inc. or its subsidiaries; the license does not grant rights
to use them except as required to describe the origin of the work.
