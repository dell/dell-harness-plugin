# Troubleshooting

## Windows authentication window

The launcher defaults to Windows PowerShell. To select an already approved PowerShell 7 host, set `$env:DEVSPACES_POWERSHELL = 'C:\path\to\pwsh.exe'` before invocation. Inspect the chosen host's effective policy; do not change policy or substitute hosts to defeat a managed restriction. `Restricted` blocks scripts before the helper starts, so the `.cmd` wrapper also pauses on nonzero exit. Use an administrator-approved execution method when policy prevents launch.

Use the bundled `scripts/launch-auth.cmd` from Explorer, or run:

```powershell
& 'C:\path\to\plugin\scripts\launch-auth.cmd' -ConnectorPath 'C:\Tools\Devspaces\connector.exe' -ClusterUrl 'https://your-cluster.example'
```

The launcher resolves its helper relative to itself and prompts locally for missing required arguments. It runs `powershell.exe -NoProfile -File` with `-PauseOnCompletion`, so completion and validation errors stay visible until Enter. Do not use `-NoExit`, disable auto-loading, or bypass execution policy. Provide only an absolute approved `.exe` path and credential-free HTTPS origin; enter the Developer Workspace URI only at the connector's private terminal prompt. Neither wrapper logs credentials. A zero exit is only command completion; recheck workspace access.

For a previously authorized certificate-error retry, explicitly add `-AllowInsecureTls`; it is never enabled by default. The helper restores its prior process TLS environment before the completion pause. If launching from Codex produces no visible window, a live helper PID alone is not proof of visibility. Stop only that verified owned helper/auth child, preserve existing tunnels, and ask the user to open the launcher manually rather than opening duplicate sessions.

- **PowerShell helper errors:** do not set `$PSModuleAutoLoadingPreference='None'` to suppress PSReadLine; it also prevents auto-loading `Write-Host`. When login is actually required, use the bundled [interactive helper](../../../scripts/authenticate_devspaces.ps1) via `powershell.exe -NoProfile -File`, with `-ConnectorPath` and `-ClusterUrl` from the plugin. Add `-AllowInsecureTls` only for an already authorized certificate retry. It uses .NET console output, preserves process TLS settings, and does not change trusted publishers or execution policy. `AbortError` / `ABORT_ERR` after Ctrl+C means that login attempt was cancelled; it does not indicate that another process's tunnel failed. Never infer completed authentication from exit code zero alone; recheck workspace access.

- **Startup:** provide Python and mcp. Set DEVSPACES_CONNECTOR_COMMAND to the approved executable's absolute path. This is a path, not a shell command with arguments.
- **Auth waits:** --auth can prompt for a Developer Workspace URI. Check the existing session first; verified access to the configured cluster means login and this prompt can be skipped entirely. If an unnecessary auth terminal is already waiting, the user may cancel it with Ctrl+C after existing access is verified. If login really is required, complete the prompt in the local terminal; do not send a blank/fabricated URI. Returned instructions and the prompt itself are not proof of completed authentication. An empty list alone does not prove expired credentials.
- **Certificates:** prefer an approved CA PEM through NODE_EXTRA_CA_CERTS. For a known certificate-verification failure (such as "unable to verify the first certificate"), offer one explicitly authorized TLS-disabled retry as described below. A PSReadLine publisher warning is a separate PowerShell trust issue; this retry does not fix it.
- **Wrong cluster:** authenticate to DEVSPACES_CLUSTER_URL and list again. The origin must match returned clusterId; empty lists cannot verify it.
- **Writes blocked:** start/stop/restart require DEVSPACES_ENABLE_WRITES; deletion additionally requires DEVSPACES_ENABLE_DELETE. Restart the MCP server after environment changes.
- **Restart timeout:** inspect phase and desired started state. If stopping did not complete, start is not issued. Do not loop retries. Events/logs require the optional Kubernetes adapter or dashboard.
- **Tunnel pending/exited:** inspect get_connection_status. For interactive diagnosis, disconnect the managed tunnel first and keep auth output private. Ready means setup was reported and the process is alive, not that an SSH probe passed.
- **Disconnected after restart:** reconnect and verify the Codex SSH session with its alias. Never hard-code example localhost ports.
- **Not managed:** this MCP server has no owned process for that workspace; other manual tunnels or SSH sessions may still exist.

## Explicit TLS retry

For a certificate-verification failure only, explain that the retry disables certificate verification and can permit interception. If the user already requested this exception, proceed without asking again. Otherwise ask before enabling it. Do not automatically downgrade TLS on a generic security error, 401/403, timeout, or network failure.

Before interactive authentication, retry the read-only session/list check with the authorized TLS exception. If existing access is verified, skip authentication. The Node.js warning explains the TLS setting; it is not an authentication error. Only if login is needed, scope the requested environment variable to the connector command and restore it even on failure:

```powershell
$devspacesPreviousTls = $env:NODE_TLS_REJECT_UNAUTHORIZED
try {
    $env:NODE_TLS_REJECT_UNAUTHORIZED = "0"
    & $env:DEVSPACES_CONNECTOR_COMMAND --auth $env:DEVSPACES_CLUSTER_URL
    if ($LASTEXITCODE -ne 0) { throw 'Devspaces authentication failed; do not report success.' }
}
finally {
    if ($null -eq $devspacesPreviousTls) {
        Remove-Item Env:NODE_TLS_REJECT_UNAUTHORIZED -ErrorAction SilentlyContinue
    } else {
        $env:NODE_TLS_REJECT_UNAUTHORIZED = $devspacesPreviousTls
    }
}
```

The connector path and cluster variables must be populated from get_devspaces_authentication_instructions first. Keep the Developer Workspace URI input private in that terminal.

For subsequent plugin list/connect calls in the same authorized session, launch the plugin process with DEVSPACES_ALLOW_INSECURE_TLS=true. Its runtime reports tls_verification_enabled=false; the adapter sets NODE_TLS_REJECT_UNAUTHORIZED=0 only for connector child processes. An ambient NODE_TLS_REJECT_UNAUTHORIZED=0 alone is still ignored by the plugin. Do not write the exception into the plugin manifest or persistent user/machine settings. Clear the opt-in and relaunch the plugin for verified TLS; existing child processes retain their launch environment until they exit. A long-lived tunnel using this exception keeps it for that tunnel's lifetime. Do not disconnect an active session unexpectedly; restore normal trust on the next planned reconnect.

After a failed retry, stop repeating login attempts and report the sanitized error category. Do not broaden the exception to SSH host-key checks or Windows trusted-publisher policy.
