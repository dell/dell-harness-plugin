# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

# Interactive terminal helper; never capture the Developer Workspace URI in chat/logs.
# Launch with powershell.exe -NoProfile -File <this file> rather than an interactive
# -NoExit shell. Do not disable module auto-loading or change execution policy.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $ConnectorPath,
    [Parameter(Mandatory = $true)]
    [string] $ClusterUrl,
    [switch] $AllowInsecureTls,
    [switch] $PauseOnCompletion
)

$devspacesPreviousTls = [Environment]::GetEnvironmentVariable('NODE_TLS_REJECT_UNAUTHORIZED', 'Process')
$devspacesExitCode = 1
try {
    if (-not [System.IO.Path]::IsPathRooted($ConnectorPath) -or
        -not [System.IO.File]::Exists($ConnectorPath) -or
        [System.IO.Path]::GetExtension($ConnectorPath) -ne '.exe') {
        [Console]::WriteLine('Provide an absolute path to the approved connector executable (.exe).')
        return
    }
    $devspacesOrigin = $null
    if (-not [uri]::TryCreate($ClusterUrl, [UriKind]::Absolute, [ref] $devspacesOrigin) -or
        $devspacesOrigin.Scheme -ne 'https' -or -not $devspacesOrigin.Host -or
        $devspacesOrigin.UserInfo -or $devspacesOrigin.Query -or $devspacesOrigin.Fragment -or
        $devspacesOrigin.AbsolutePath -ne '/') {
        [Console]::WriteLine('Use the configured credential-free HTTPS cluster origin.')
        return
    }
    if ($AllowInsecureTls) {
        [Console]::WriteLine('Explicit TLS exception: certificate verification is disabled for this connector invocation.')
        [Environment]::SetEnvironmentVariable('NODE_TLS_REJECT_UNAUTHORIZED', '0', 'Process')
    } else {
        [Environment]::SetEnvironmentVariable('NODE_TLS_REJECT_UNAUTHORIZED', $null, 'Process')
    }
    [Console]::WriteLine('Complete required login prompts only in this terminal. Do not paste the Developer Workspace URI into chat.')
    $LASTEXITCODE = $null
    & $ConnectorPath --auth $devspacesOrigin.AbsoluteUri.TrimEnd('/')
    $devspacesAuthExit = $LASTEXITCODE
    if ($null -eq $devspacesAuthExit -or $devspacesAuthExit -ne 0) {
        [Console]::WriteLine('Authentication command failed or was interrupted. Return to Codex to recheck access before retrying.')
    } else {
        $devspacesExitCode = 0
        # Some connector builds may exit zero even after cancellation.
        [Console]::WriteLine('Authentication command ended. This does not confirm login; return to Codex to verify existing workspace access.')
    }
}
catch {
    # Exception details may contain connector output or sensitive input.
    [Console]::WriteLine('Unable to complete authentication. Check the executable and local terminal, then return to Codex.')
}
finally {
    [Environment]::SetEnvironmentVariable('NODE_TLS_REJECT_UNAUTHORIZED', $devspacesPreviousTls, 'Process')
    if ($PauseOnCompletion) {
        [Console]::WriteLine('Press Enter to close this window. Login is not verified until Codex rechecks workspace access.')
        [void][Console]::ReadLine()
    }
    exit $devspacesExitCode
}
