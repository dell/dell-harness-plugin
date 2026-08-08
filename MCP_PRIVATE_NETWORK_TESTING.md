# Testing private DDAE and DDSE MCP endpoints

The existing Dev Spaces setup forwards an SSH service to the desktop. It does
not forward either MCP HTTPS endpoint. For the most reliable test, run Codex on
the SSH/Dev Spaces host that can resolve and reach the private MCP hostnames.
The desktop app can then connect to that host over SSH; MCP servers, plugins,
credentials, and network access come from the remote host.

## Recommended: execute Codex on the network-adjacent host

1. Add a concrete SSH host alias and verify `ssh <alias>` works.
2. Install and authenticate Codex on that remote host.
3. Export `DDAE_MCP_URL`, `DDAE_OAUTH_RESOURCE`, `DDSE_MCP_URL`, and
   `DDSE_MCP_AUTHORIZATION` in the environment that starts remote Codex.
4. Add this marketplace and both plugins on the remote host.
5. Complete DDAE OAuth, start Codex, and use `/mcp` to inspect both servers.

When using the CLI and a browser on the local desktop, use a fixed remote OAuth
callback port and forward that port through SSH:

```bash
ssh -J <jump-user>@<jump-host> \
  -L 5555:127.0.0.1:5555 \
  <remote-user>@<remote-host>

# Run on the remote host:
codex -c mcp_oauth_callback_port=5555 \
  mcp login dell_data_analytics_engine --scopes openid
```

The browser callback to local port 5555 traverses the SSH forward to the Codex
process on the remote host.

## Optional connectivity diagnostic: dynamic SOCKS tunnel

A SOCKS tunnel preserves the original HTTPS hostname and remote DNS lookup:

```bash
ssh -N -D 127.0.0.1:1080 \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -J <jump-user>@<jump-host> \
  <remote-user>@<remote-host>
```

From a second terminal, validate routing without disabling TLS verification:

```bash
curl --proxy socks5h://127.0.0.1:1080 \
  -i "https://<ddae-host>/.well-known/oauth-protected-resource"

curl --proxy socks5h://127.0.0.1:1080 \
  -i "https://<kibana-host>/api/agent_builder/mcp"
```

An authentication challenge is evidence that network routing, DNS, TLS, and
the endpoint path work. It is not evidence that authorization is complete.

Do not replace the MCP URLs with `https://localhost:<port>/...`. A plain local
port forward changes the hostname used for TLS and can break certificate
validation, OAuth protected-resource discovery, issuer/audience checks, and
redirect handling. If a local Codex build is launched through an upstream
SOCKS proxy, verify that build's proxy support explicitly; otherwise use the
network-adjacent-host method above.
