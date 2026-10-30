@echo off
REM SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
REM SPDX-License-Identifier: Apache-2.0

rem Native CLI authentication; no PowerShell script execution is required.
rem Supply --connector and --cluster, or their DEVSPACES environment variables.
setlocal
set "devspacesPython=python"
if defined DEVSPACES_PYTHON_COMMAND set "devspacesPython=%DEVSPACES_PYTHON_COMMAND%"
"%devspacesPython%" "%~dp0authenticate_devspaces.py" --foreground --pause %*
set "devspacesAuthExit=%errorlevel%"
if not "%devspacesAuthExit%"=="0" (
  echo CLI authentication is not verified. Return to the agent to check access.
  pause
)
exit /b %devspacesAuthExit%
