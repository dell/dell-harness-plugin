# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Test-only synthetic connector behind the REAL plugin MCP transport and tools.

No network, credentials, or real connector processes. Not an agent evaluation.
"""
import io
import json
import os
import sys
import tempfile
from pathlib import Path

plugin_root = Path(os.getenv('DEVSPACES_TEST_PLUGIN_ROOT', str(Path(__file__).parents[2])))
ssh_temporary = tempfile.TemporaryDirectory(prefix='devspaces-synthetic-ssh-')
os.environ['DEVSPACES_SSH_CONFIG'] = str(Path(ssh_temporary.name) / 'config')
Path(os.environ['DEVSPACES_SSH_CONFIG']).write_text('')
sys.path.insert(0, str(plugin_root / 'scripts'))
import connector_mcp_server as server

scenario = os.getenv('DEVSPACES_TEST_SCENARIO', 'normal')
rows = [dict(name=name, namespace='review-team', clusterId='devspaces.example.com',
             devworkspaceId='workspace-' + name, phase='Stopped', started=False,
             displayName=name, creationTimestamp='2026-09-30T12:00:00Z')
        for name in ('alpha', 'beta')]
if scenario == 'wrong_cluster':
    rows[0]['clusterId'] = 'another.example'
if scenario == 'empty':
    rows.clear()


def run(args, timeout=30):
    if args == ['--version']:
        return 'synthetic-fixture-v1'
    if args == ['--list']:
        return json.dumps(rows)
    target = next(row for row in rows if row['name'] == args[1])
    if args[0] == '--delete':
        rows.remove(target)
    elif args[0] in ('--start', '--stop'):
        target['started'] = args[0] == '--start'
        target['phase'] = 'Running' if target['started'] else 'Stopped'
    else:
        raise AssertionError('Unexpected synthetic command')
    return ''


class Tunnel:
    pid = 999
    code = None

    def __init__(self, *args, **kwargs):
        self.stdout = io.StringIO('Connection setup completed!\n')

    def poll(self): return self.code
    def terminate(self): self.code = 0
    def wait(self, timeout): return self.code
    def kill(self): self.code = -1


server.connector.run = run
server.connector.executable = lambda: 'synthetic-connector'
server.subprocess.Popen = Tunnel
server.mcp.run(transport='stdio')
