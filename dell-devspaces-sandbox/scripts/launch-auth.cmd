@echo off
REM SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
REM SPDX-License-Identifier: Apache-2.0

rem Supply only the approved executable path and credential-free cluster origin.
rem With no arguments, PowerShell prompts locally for the required parameters.
set "devspacesPowerShell=powershell.exe"
if defined DEVSPACES_POWERSHELL set "devspacesPowerShell=%DEVSPACES_POWERSHELL%"
"%devspacesPowerShell%" -NoProfile -File "%~dp0authenticate_devspaces.ps1" -PauseOnCompletion %*
set "devspacesAuthExit=%errorlevel%"
if not "%devspacesAuthExit%"=="0" (
  echo Authentication did not complete. If PowerShell blocked the helper, ask your administrator for an approved script execution method; do not bypass policy.
  pause
)
exit /b %devspacesAuthExit%
