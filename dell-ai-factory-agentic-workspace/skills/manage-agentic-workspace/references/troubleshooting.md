# Troubleshooting

## Native authentication

Prefer `begin_devspaces_authentication` and `get_devspaces_authentication_progress`. During requested setup an unverified session triggers native login without a second user instruction. Read progress and recheck CLI access yourself; do not ask the user to confirm terminal success. The agent can also run `scripts/authenticate_devspaces.py --connector <absolute-executable> --cluster <HTTPS-origin>` using its resolved Python interpreter. On Windows it creates a native console; the CLI opens the machine's default browser for SSO. Never authenticate through ChatGPT Desktop's in-app browser. Browser sign-in and CLI access are distinct: always verify using `get_authentication_status` and `list_devspaces` after interactive authentication.

The helper needs only Python's standard library. It checks `--list` first, skips login for a verified cluster, inherits the terminal for `--auth`, and checks `--list` afterward. Empty inventory is inconclusive and returns a distinct unverified result after login. Credentials and Developer Workspace URIs are entered privately, never as command arguments or captured output. The launcher reports `state=launched`, not successful authentication.

If `.ps1` execution is blocked, run the Python helper directly. The `.cmd` wrapper also invokes Python directly and accepts `--connector` and `--cluster`; do not paste batch-file contents into PowerShell. The optional PowerShell wrapper accepts `-ConnectorPath`, `-ClusterUrl`, and `-PythonPath`. None of these launchers changes execution policy or trusted publishers. Do not switch PowerShell hosts to evade managed policy.

If the launched console is absent, inspect only the owned process and check CLI access before cleanup: authentication may already have completed. If it is still blocked, stop only that helper and owned auth child, preserving tunnels. Use `--foreground --pause` in a controllable existing native terminal. Ask the user to perform a local action only after identifying a concrete UI/tool/policy limitation; never offload routine commands or dependency checks.

## Developer Workspace URI prompt after browser SSO

If the machine browser has completed SSO but the native terminal is still waiting at `Please enter the Developer Workspace URI:`, the user may press **Enter once** with the field empty to let this connector attempt finish without supplying a URI. This is a completion attempt, not proof that the prompt is optional or that authentication succeeded; connector versions can differ and an empty entry may cancel the attempt. Do not loop blank submissions or fabricate a URI. If a URI is actually required, enter it privately in the native terminal.

After Enter, the agent must immediately recheck CLI authentication and workspace inventory. Proceed only when the intended cluster and workspace access are verified. An empty list, zero exit status, or completed browser SSO alone is insufficient. Do not hard-code automatic newline injection into the authentication helper.

## Certificate failures

Certificate verification is never disabled automatically. A certificate-verification error during `--list`, `--auth`, a lifecycle action, or `--connect` fails with `certificate verification failed` and nothing is retried. A failed or cancelled sign-in leaves TLS exactly as it was.

Workarounds, applied only with the user's choice through `scripts/configure_devspaces.py` (settings file `~/.config/dell-mcp/devspaces.json`, user-only):

| Situation | Command | Verification |
| --- | --- | --- |
| Organization CA PEM available | `--ca-bundle <path>` | On |
| Self-signed cluster certificate | `--cluster-url <origin> --fetch-cluster-certificate`, then confirm the fingerprint with the administrator | On, trusting that certificate |
| Lab cluster, user accepts interception risk | `--allow-insecure-tls` (undo with `--no-allow-insecure-tls`) | Off for every call |

`--fetch-cluster-certificate` pins the certificate the cluster presents; it does not help when that certificate is issued by a private CA, so use the CA file instead. Settings are read at plugin start: start a new Codex session after a change and confirm `tls_verification_enabled` in `get_runtime_configuration`. `DEVSPACES_AUTO_TLS_RETRY` was removed in 0.6.0; ambient `NODE_TLS_REJECT_UNAUTHORIZED` is ignored.

Run the read-only check before any authentication. If it verifies access, skip login. Standard input and output stay attached to the native terminal; stderr is forwarded immediately there while checking only the certificate-error category, without retaining or logging it. Never invoke interactive foreground authentication through a tool that captures terminal output; use a native console. No loop, generic-error downgrade, or lifecycle/delete replay is allowed.

## Runtime and workspace checks

- **Missing Python/MCP dependency:** resolve the configured interpreter and run import checks yourself. A sandbox access denial is not proof that a dependency is missing; use the permitted escalation path. Install dependencies only into the intended environment with required authorization. Do not modify plugin-cache files or reuse ad hoc user-specific dependency paths.
- **No MCP tools in chat:** retain a configured MCP stdio client session and call the plugin tools from it. Do not create a fresh one-shot server for each tunnel check. `connector_mcp_server.py --check` is available for read-only diagnosis; `authenticate_devspaces.py --check` checks CLI access without MCP dependencies and never starts login.
- **Empty inventory:** report the current CLI result; verify origin, namespace filters, and account evidence. Do not infer expired login or open the dashboard to check status. Offer native CLI authentication when the user requests it or evidence justifies it.
- **Wrong cluster:** preserve existing tunnels; configure the requested origin in a separate session and authenticate natively when needed.
- **Writes blocked:** start/stop/restart require `DEVSPACES_ENABLE_WRITES`; deletion also requires `DEVSPACES_ENABLE_DELETE`. Do not silently enable gates.
- **Timeout:** inspect workspace phase and identity before retrying an uncertain mutation. Do not replay start/stop/delete automatically.
- **Tunnel pending/exited:** check `get_connection_status` for up to 30 seconds. Ready means setup was reported and the owned process is alive; run a separate SSH login check using the effective CLI/user policy.
- **Unknown or changed SSH host key:** stop and report it; do not accept, replace, or remove the pinned key and do not reconnect to bypass it. The plugin never edits SSH configuration. Verify the new key through the user's or organization's approved process before anyone updates known hosts. TLS settings do not establish SSH identity.
- **Connection refused after workspace restart:** a Running workspace does not recreate a local tunnel. Recheck identity and invoke `connect_devspace`; use its refreshed alias/port rather than a stale localhost port.
- **Not managed:** other manually launched tunnels may exist. Disconnect only this server's owned tunnel; keep the owning MCP process alive while connected.


## Silent empty inventory and Python recovery

An empty `--list` is not evidence that certificate trust passed or that the
account has no workspaces. Check the configured CA bundle and cluster origin,
complete native login, and report the empty result; never retry the list with
certificate verification disabled.

Use the bundled bootstrap and retained client instead of writing new runtime or
MCP-client scripts in each chat. The bootstrap checks real stdio imports, installs
only the default connector's pinned MCP dependency into an isolated reusable
venv when needed, and keeps protocol stdout clean. It never weakens pip TLS or
installs into the plugin cache. See [setup scenarios](setup-scenarios.md).
