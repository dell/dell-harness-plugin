# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

# Convenience wrapper; use the Python helper directly when .ps1 execution is blocked.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string] $ConnectorPath,
    [Parameter(Mandatory = $true)] [string] $ClusterUrl,
    [string] $PythonPath = 'python',
    [switch] $AllowInsecureTls,
    [switch] $PauseOnCompletion
)
$devspacesArguments = @(
    (Join-Path $PSScriptRoot 'authenticate_devspaces.py'),
    '--foreground', '--connector', $ConnectorPath, '--cluster', $ClusterUrl
)
if ($AllowInsecureTls) { $devspacesArguments += '--allow-insecure-tls' }
if ($PauseOnCompletion) { $devspacesArguments += '--pause' }
& $PythonPath @devspacesArguments
exit $LASTEXITCODE
