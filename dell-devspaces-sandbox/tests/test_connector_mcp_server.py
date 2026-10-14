# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import asyncio
import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

PATH = Path(__file__).parents[1] / 'scripts' / 'connector_mcp_server.py'
SPEC = importlib.util.spec_from_file_location('connector_mcp_server', PATH)
SERVER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SERVER
SPEC.loader.exec_module(SERVER)


@pytest.fixture
def adapter(monkeypatch):
    for name in ('DEVSPACES_CLUSTER_URL', 'DEVSPACES_ALLOWED_NAMESPACES',
                 'DEVSPACES_ENABLE_WRITES', 'DEVSPACES_ENABLE_DELETE', 'DEVSPACES_ALLOW_INSECURE_TLS'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('DEVSPACES_CLUSTER_URL', 'https://devspaces.example.com')
    return SERVER.Connector()


def test_unconfigured_cluster_fails_closed_without_a_default(monkeypatch):
    monkeypatch.delenv('DEVSPACES_CLUSTER_URL', raising=False)
    adapter = SERVER.Connector()
    assert adapter.cluster == '' and adapter.cluster_id == ''
    monkeypatch.setattr(adapter, 'run', lambda args: pytest.fail('connector must not be invoked'))
    with pytest.raises(RuntimeError, match='DEVSPACES_CLUSTER_URL'):
        adapter.list()
    with pytest.raises(RuntimeError, match='DEVSPACES_CLUSTER_URL'):
        adapter.require_cluster()


def row(phase='Running', **changes):
    return dict(name='devbox', namespace='team', clusterId='devspaces.example.com',
                devworkspaceId='workspace123', phase=phase, started=phase == 'Running', **changes)


def test_list_redacts_extra_fields_and_checks_cluster(adapter, monkeypatch):
    monkeypatch.setattr(adapter, 'run', lambda args: json.dumps([row(token='secret')]))
    assert 'token' not in adapter.list()[0]
    data = row()
    data['clusterId'] = 'other.example'
    monkeypatch.setattr(adapter, 'run', lambda args: json.dumps([data]))
    with pytest.raises(RuntimeError, match='cluster'):
        adapter.list()


@pytest.mark.parametrize('output', ['not json', '{}', '[1]'])
def test_invalid_list_fails_closed(adapter, monkeypatch, output):
    monkeypatch.setattr(adapter, 'run', lambda args: output)
    with pytest.raises(RuntimeError):
        adapter.list()


def test_duplicate_names_outside_filter_still_block_mutation(adapter, monkeypatch):
    adapter.namespaces = ('team',)
    second = row()
    second['namespace'] = 'another'
    monkeypatch.setattr(adapter, 'run', lambda args: json.dumps([row(), second]))
    assert len(adapter.list()) == 1
    with pytest.raises(ValueError, match='uniquely'):
        adapter.workspace('devbox', 'team')
    with pytest.raises(PermissionError):
        adapter.workspace('devbox', 'another')


def test_read_only_gate_prevents_commands(adapter, monkeypatch):
    monkeypatch.setattr(adapter, 'run', lambda *args: pytest.fail('must not run'))
    with pytest.raises(PermissionError):
        adapter.lifecycle('devbox', 'team', 'start')


def test_restart_observes_stop_before_start(adapter, monkeypatch):
    adapter.writes = True
    calls = []
    states = iter([row(), row('Stopping'), row('Stopped'), row('Starting'), row()])
    def run(args):
        calls.append(args)
        return json.dumps([next(states)]) if args == ['--list'] else ''
    monkeypatch.setattr(adapter, 'run', run)
    monkeypatch.setattr(SERVER.time, 'sleep', lambda _: None)
    assert adapter.lifecycle('devbox', 'team', 'restart')['phase'] == 'Running'
    assert calls == [['--list'], ['--stop', 'devbox'], ['--list'], ['--list'],
                     ['--start', 'devbox'], ['--list'], ['--list']]


def test_stop_timeout_never_starts(adapter, monkeypatch):
    adapter.writes = True
    calls = []
    def run(args):
        calls.append(args)
        return json.dumps([row()]) if args == ['--list'] else ''
    monkeypatch.setattr(adapter, 'run', run)
    times = iter([0, 1000])
    monkeypatch.setattr(SERVER.time, 'monotonic', lambda: next(times))
    with pytest.raises(TimeoutError):
        adapter.lifecycle('devbox', 'team', 'restart', 1)
    assert ['--start', 'devbox'] not in calls


def test_replaced_workspace_never_starts(adapter, monkeypatch):
    adapter.writes = True
    changed = row('Stopped')
    changed['devworkspaceId'] = 'replacement'
    states = iter([row(), changed])
    calls = []
    def run(args):
        calls.append(args)
        return json.dumps([next(states)]) if args == ['--list'] else ''
    monkeypatch.setattr(adapter, 'run', run)
    with pytest.raises(RuntimeError, match='identity changed'):
        adapter.lifecycle('devbox', 'team', 'restart')
    assert ['--start', 'devbox'] not in calls


def test_tls_and_process_error_redaction(adapter, monkeypatch):
    monkeypatch.setenv('NODE_TLS_REJECT_UNAUTHORIZED', '0')
    monkeypatch.setenv('NODE_EXTRA_CA_CERTS', 'approved.pem')
    monkeypatch.setattr(adapter, 'executable', lambda: 'C:/Program Files/connector.exe')
    def run(args, **kwargs):
        assert args == ['C:/Program Files/connector.exe', '--list']
        assert 'NODE_TLS_REJECT_UNAUTHORIZED' not in kwargs['env']
        assert kwargs['env']['NODE_EXTRA_CA_CERTS'] == 'approved.pem'
        assert kwargs['stdin'] == subprocess.DEVNULL
        return subprocess.CompletedProcess(args, 1, 'secret', 'secret')
    monkeypatch.setattr(SERVER.subprocess, 'run', run)
    with pytest.raises(RuntimeError) as exc:
        adapter.run(['--list'])
    assert 'secret' not in str(exc.value)


def test_connect_reuses_owned_process_and_disconnects(adapter, monkeypatch):
    monkeypatch.setattr(adapter, 'workspace', lambda *args: row())
    monkeypatch.setattr(adapter, 'executable', lambda: 'connector')
    class Process:
        pid = 123
        code = None
        stdout = io.StringIO('private output\nConnection setup completed! Please connect to SSH Host alias: devbox\n')
        def poll(self): return self.code
        def terminate(self): self.code = 0
        def wait(self, timeout): return self.code
    process = Process()
    calls = []
    def popen(args, **kwargs):
        calls.append(args)
        return process
    class Thread:
        def __init__(self, target, daemon): self.target = target
        def start(self): self.target()
    monkeypatch.setattr(SERVER.subprocess, 'Popen', popen)
    monkeypatch.setattr(SERVER.threading, 'Thread', Thread)
    assert adapter.connect('devbox', 'team')['state'] == 'ready'
    assert adapter.connect('devbox', 'team')['ssh_alias'] == 'devbox'
    assert calls == [['connector', '--connect', 'devbox']]
    adapter.close()
    assert process.poll() == 0
    assert adapter.connection_status('devbox', 'team')['state'] == 'not_managed'


def test_connect_stopped_workspace_does_not_spawn(adapter, monkeypatch):
    monkeypatch.setattr(adapter, 'workspace', lambda *args: row('Stopped'))
    monkeypatch.setattr(SERVER.subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('must not spawn'))
    with pytest.raises(RuntimeError, match='Start'):
        adapter.connect('devbox', 'team')


def test_auth_returns_instructions_without_launch(adapter, monkeypatch):
    monkeypatch.setattr(SERVER, 'connector', adapter)
    monkeypatch.setattr(adapter, 'executable', lambda: 'connector')
    monkeypatch.setattr(SERVER.subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('must not spawn'))
    assert SERVER.get_devspaces_authentication_instructions()['interactive_required'] is True
    monkeypatch.setattr(adapter, 'list', lambda: [])
    assert SERVER.get_authentication_status() == dict(list_accessible=True, cluster_verified=False, workspace_count=0)


def test_delete_requires_both_gates_and_exact_confirmation(adapter, monkeypatch):
    monkeypatch.setattr(SERVER, 'connector', adapter)
    adapter.deletion = True
    with pytest.raises(PermissionError):
        SERVER.delete_devspace('team', 'devbox', 'devbox', 'workspace123')
    adapter.writes = True
    with pytest.raises(ValueError):
        SERVER.delete_devspace('team', 'devbox', 'wrong', 'workspace123')
    calls = []
    monkeypatch.setattr(adapter, 'workspace', lambda *args: row())
    monkeypatch.setattr(adapter, 'run', lambda args: calls.append(args))
    assert SERVER.delete_devspace('team', 'devbox', 'devbox', 'workspace123')['deletion_requested']
    assert calls == [['--delete', 'devbox']]


def test_mcp_tool_schemas_load():
    tools = asyncio.run(SERVER.mcp.list_tools())
    by_name = {tool.name: tool for tool in tools}
    assert {'start_devspace', 'stop_devspace', 'restart_devspace', 'connect_devspace',
            'disconnect_devspace', 'get_devspace_status'} <= by_name.keys()
    assert by_name['restart_devspace'].annotations.idempotentHint is False
    assert by_name['list_devspaces'].annotations.readOnlyHint is True


@pytest.mark.parametrize('field,value', [('started', 'false'), ('name', None),
                                       ('devworkspaceId', ''), ('phase', 1),
                                       ('displayName', {'token': 'secret'})])
def test_malformed_workspace_fields_rejected(adapter, monkeypatch, field, value):
    data = row()
    data[field] = value
    monkeypatch.setattr(adapter, 'run', lambda args: json.dumps([data]))
    with pytest.raises(RuntimeError, match='fields'):
        adapter.list()


def test_expected_identity_checked_before_action(adapter, monkeypatch):
    adapter.writes = True
    monkeypatch.setattr(adapter, 'workspace', lambda *args: row())
    monkeypatch.setattr(adapter, 'run', lambda *args: pytest.fail('must not mutate'))
    with pytest.raises(RuntimeError, match='identity changed'):
        adapter.lifecycle('devbox', 'team', 'restart', expected_identity='old')
    with pytest.raises(RuntimeError, match='identity changed'):
        adapter.connect('devbox', 'team', expected_identity='old')


def test_start_running_is_noop(adapter, monkeypatch):
    adapter.writes = True
    monkeypatch.setattr(adapter, 'workspace', lambda *args: row())
    monkeypatch.setattr(adapter, 'run', lambda *args: pytest.fail('must not mutate'))
    assert adapter.lifecycle('devbox', 'team', 'start')['phase'] == 'Running'


def test_subprocess_timeout_does_not_leak_output(adapter, monkeypatch):
    monkeypatch.setattr(adapter, 'executable', lambda: 'connector')
    def run(*args, **kwargs):
        raise subprocess.TimeoutExpired('connector', 30, output='secret')
    monkeypatch.setattr(SERVER.subprocess, 'run', run)
    with pytest.raises(RuntimeError) as exc:
        adapter.run(['--stop', 'devbox'])
    assert 'secret' not in str(exc.value)
    assert 'recheck status' in str(exc.value)


def test_disconnect_escalates_only_owned_process(adapter):
    class Process:
        pid = 123
        killed = False
        def poll(self): return None
        def terminate(self): pass
        def wait(self, timeout):
            if not self.killed: raise subprocess.TimeoutExpired('fixture', timeout)
        def kill(self): self.killed = True
    process = Process()
    adapter.connections[('team', 'devbox')] = {'process': process}
    adapter.disconnect('devbox', 'team')
    assert process.killed
    assert not adapter.connections


def test_explicit_tls_retry_scoped_to_child_environment(monkeypatch):
    monkeypatch.setenv('DEVSPACES_CLUSTER_URL', 'https://devspaces.example.com')
    monkeypatch.setenv('DEVSPACES_ALLOW_INSECURE_TLS', 'true')
    monkeypatch.setenv('NODE_TLS_REJECT_UNAUTHORIZED', '1')
    adapter = SERVER.Connector()
    assert adapter.environment()['NODE_TLS_REJECT_UNAUTHORIZED'] == '0'
    assert SERVER.os.environ['NODE_TLS_REJECT_UNAUTHORIZED'] == '1'
    monkeypatch.setattr(SERVER, 'connector', adapter)
    monkeypatch.setattr(adapter, 'executable', lambda: 'connector')
    monkeypatch.setattr(adapter, 'run', lambda args: 'test-version')
    assert SERVER.get_runtime_configuration()['tls_verification_enabled'] is False
    assert SERVER.get_devspaces_authentication_instructions()['environment'] == {'NODE_TLS_REJECT_UNAUTHORIZED': '0'}
    monkeypatch.delenv('DEVSPACES_ALLOW_INSECURE_TLS')
    assert 'NODE_TLS_REJECT_UNAUTHORIZED' not in SERVER.Connector().environment()


def test_certificate_error_category_is_sanitized(adapter, monkeypatch):
    monkeypatch.setattr(adapter, 'executable', lambda: 'connector')
    monkeypatch.setattr(SERVER.subprocess, 'run', lambda *args, **kwargs:
                        subprocess.CompletedProcess([], 1, '', 'unable to verify the first certificate; token=secret'))
    with pytest.raises(RuntimeError) as exc:
        adapter.run(['--list'])
    assert 'certificate verification failed' in str(exc.value)
    assert 'secret' not in str(exc.value)
