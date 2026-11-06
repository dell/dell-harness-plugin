# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import asyncio
import importlib.util
import io
import json
import secrets
import subprocess
import sys
from pathlib import Path

import pytest

# Generated per run: no credential-shaped literal in source, and redaction checks cannot pass by luck.
SENTINEL = secrets.token_urlsafe(12)
PATH = Path(__file__).parents[1] / 'scripts' / 'connector_mcp_server.py'
SPEC = importlib.util.spec_from_file_location('connector_mcp_server', PATH)
SERVER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SERVER
SPEC.loader.exec_module(SERVER)


@pytest.fixture
def adapter(monkeypatch, tmp_path):
    for name in ('DEVSPACES_CLUSTER_URL', 'DEVSPACES_ALLOWED_NAMESPACES',
                 'DEVSPACES_ENABLE_WRITES', 'DEVSPACES_ENABLE_DELETE', 'DEVSPACES_ALLOW_INSECURE_TLS'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('DEVSPACES_CLUSTER_URL', 'https://devspaces.example.com')
    ssh_config = tmp_path / 'ssh_config'
    ssh_config.write_text('')
    monkeypatch.setenv('DEVSPACES_SSH_CONFIG', str(ssh_config))
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
    monkeypatch.setattr(adapter, 'run', lambda args: json.dumps([row(token=SENTINEL)]))
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
        return subprocess.CompletedProcess(args, 1, SENTINEL, SENTINEL)
    monkeypatch.setattr(SERVER.subprocess, 'run', run)
    with pytest.raises(RuntimeError) as exc:
        adapter.run(['--list'])
    assert SENTINEL not in str(exc.value)


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
                                       ('displayName', {'token': SENTINEL})])
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
        raise subprocess.TimeoutExpired('connector', 30, output=SENTINEL)
    monkeypatch.setattr(SERVER.subprocess, 'run', run)
    with pytest.raises(RuntimeError) as exc:
        adapter.run(['--stop', 'devbox'])
    assert SENTINEL not in str(exc.value)
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
                        subprocess.CompletedProcess([], 1, '', f'unable to verify the first certificate; token={SENTINEL}'))
    with pytest.raises(RuntimeError) as exc:
        adapter.run(['--list'])
    assert 'certificate verification failed' in str(exc.value)
    assert SENTINEL not in str(exc.value)


@pytest.mark.parametrize('args', [['--list'], ['--start', 'devbox'], ['--stop', 'devbox'],
                                  ['--delete', 'devbox'], ['--version']])
@pytest.mark.parametrize('auto_setting', [None, 'true'])
def test_bad_certificate_is_rejected_and_never_downgrades_tls(adapter, monkeypatch, args, auto_setting):
    if auto_setting is not None:
        monkeypatch.setenv('DEVSPACES_AUTO_TLS_RETRY', auto_setting)
        adapter = SERVER.Connector()
    monkeypatch.setattr(adapter, 'executable', lambda: 'connector')
    seen = []
    def run(command, **kwargs):
        seen.append(kwargs['env'].get('NODE_TLS_REJECT_UNAUTHORIZED'))
        return subprocess.CompletedProcess(command, 1, SENTINEL, 'self signed certificate ' + SENTINEL)
    monkeypatch.setattr(SERVER.subprocess, 'run', run)
    for _ in range(2):
        with pytest.raises(RuntimeError, match='certificate verification failed') as exc:
            adapter.run(args)
        assert SENTINEL not in str(exc.value)
    # One attempt per call, every attempt verified, and nothing carried into later calls.
    assert seen == [None, None]
    assert not adapter.insecure_tls


def test_empty_inventory_never_triggers_an_unverified_probe(adapter, monkeypatch):
    calls = []
    def run(args):
        calls.append(adapter.insecure_tls)
        return '[]'
    monkeypatch.setattr(adapter, 'run', run)
    assert adapter.list() == []
    assert adapter.list() == []
    assert calls == [False, False] and not adapter.insecure_tls


@pytest.mark.parametrize('state', ['failed', 'unverified', 'verified', 'cancelled'])
def test_sign_in_result_cannot_disable_tls_for_later_calls(adapter, monkeypatch, tmp_path, state):
    class Process:
        pid = 123
        def poll(self): return 1
    status_file = tmp_path / 'status.json'
    status_file.write_text(json.dumps({'cluster_url': adapter.cluster, 'state': state,
                                     'cluster_verified': state == 'verified',
                                     'tls_verification_enabled': False, 'terminal': SENTINEL}))
    adapter.auth_job = (Process(), status_file)
    result = adapter.authentication_progress()
    assert result['state'] == state
    assert SENTINEL not in json.dumps(result)
    assert not adapter.insecure_tls
    monkeypatch.setattr(adapter, 'executable', lambda: 'connector')
    seen = []
    def run(command, **kwargs):
        seen.append(kwargs['env'].get('NODE_TLS_REJECT_UNAUTHORIZED'))
        return subprocess.CompletedProcess(command, 0, json.dumps([row()]), '')
    monkeypatch.setattr(SERVER.subprocess, 'run', run)
    SERVER.connector, original = adapter, SERVER.connector
    try:
        assert SERVER.get_authentication_status()['cluster_verified']
    finally:
        SERVER.connector = original
    assert seen == [None]


def test_runtime_reports_no_automatic_tls_retry(adapter, monkeypatch):
    monkeypatch.setattr(SERVER, 'connector', adapter)
    monkeypatch.setattr(adapter, 'executable', lambda: 'connector')
    monkeypatch.setattr(adapter, 'run', lambda args: 'test-version')
    runtime = SERVER.get_runtime_configuration()
    assert runtime['tls_verification_enabled'] is True
    assert 'automatic_tls_retry' not in runtime


def test_begin_auth_avoids_duplicate_native_windows(adapter, monkeypatch, tmp_path):
    class Process:
        pid = 123
        def poll(self): return None
    adapter.auth_job = (Process(), tmp_path / 'status.json')
    monkeypatch.setattr(SERVER.subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('duplicate window'))
    assert adapter.begin_authentication()['pid'] == 123


def test_dead_auth_helper_is_reported_without_user_terminal_readback(adapter, tmp_path):
    class Process:
        pid = 123
        def poll(self): return 1
    adapter.auth_job = (Process(), tmp_path / 'status.json')
    assert adapter.authentication_progress()['state'] == 'failed'


def test_begin_auth_launches_native_helper_with_private_status(adapter, monkeypatch, tmp_path):
    if SERVER.os.name != 'nt':
        pytest.skip('Windows native console')
    monkeypatch.setattr(adapter, 'list', lambda: [])
    monkeypatch.setattr(adapter, 'executable', lambda: 'connector.exe')
    monkeypatch.setattr(SERVER.tempfile, 'mkdtemp', lambda **kwargs: str(tmp_path))
    calls = []
    class Process:
        pid = 123
        def poll(self): return None
    def popen(command, **kwargs):
        calls.append((command, kwargs))
        return Process()
    monkeypatch.setattr(SERVER.subprocess, 'Popen', popen)
    result = adapter.begin_authentication()
    assert result['state'] == 'launched'
    command, options = calls[0]
    assert '--status-file' in command and '--foreground' in command
    assert '--allow-insecure-tls' not in command
    assert options['creationflags'] == subprocess.CREATE_NEW_CONSOLE
    assert not any(key in options for key in ('stdin', 'stdout', 'stderr'))
    assert 'NODE_TLS_REJECT_UNAUTHORIZED' not in options['env']
    (tmp_path / 'status.json').write_text(json.dumps({'cluster_url': adapter.cluster,
                                                    'state': 'verified', 'cluster_verified': True,
                                                    'tls_verification_enabled': False}))
    assert adapter.authentication_progress()['cluster_verified']
    assert not adapter.insecure_tls


def legacy_guard(name='devbox'):
    return ('# Dell Devspaces pinned trust: ' + name + '\nHost ' + name + '\n'
            '    StrictHostKeyChecking yes\n'
            '    UserKnownHostsFile "~/.ssh/known_hosts"\n'
            'Host *\n# End Dell Devspaces pinned trust: ' + name + '\n')


PINNED = (legacy_guard() + '# User settings\nHost devbox\n  StrictHostKeyChecking yes\n'
          'Include devspaces.conf\n').encode()


class Tunnel:
    pid = 123
    def __init__(self, output='Connection setup completed!', code=None):
        self.stdout = io.StringIO(output)
        self.code = code
    def poll(self): return self.code
    def terminate(self): self.code = -15
    def wait(self, timeout): return self.code


def run_threads_inline(monkeypatch):
    class Thread:
        def __init__(self, target, daemon): self.target = target
        def start(self): self.target()
    monkeypatch.setattr(SERVER.threading, 'Thread', Thread)


def test_plugin_has_no_ssh_config_writer():
    assert not hasattr(SERVER, 'remove_legacy_ssh_trust')
    source = PATH.read_text(encoding='utf-8')
    assert 'ssh_config' not in source and 'NamedTemporaryFile' not in source


def test_connect_leaves_ssh_config_and_pins_to_the_cli(adapter, monkeypatch):
    monkeypatch.setattr(adapter, 'workspace', lambda *args: row())
    monkeypatch.setattr(adapter, 'executable', lambda: 'connector')
    config = Path(SERVER.os.environ['DEVSPACES_SSH_CONFIG'])
    config.write_bytes(PINNED)
    def popen(*args, **kwargs):
        assert config.read_bytes() == PINNED
        return Tunnel()
    monkeypatch.setattr(SERVER.subprocess, 'Popen', popen)
    run_threads_inline(monkeypatch)
    assert adapter.connect('devbox', 'team')['state'] == 'ready'
    adapter.disconnect('devbox', 'team')
    assert config.read_bytes() == PINNED


@pytest.mark.parametrize('failure', ['launch', 'exit'])
def test_failed_connect_preserves_existing_host_key_pin(adapter, monkeypatch, failure):
    monkeypatch.setattr(adapter, 'workspace', lambda *args: row())
    monkeypatch.setattr(adapter, 'executable', lambda: 'connector')
    config = Path(SERVER.os.environ['DEVSPACES_SSH_CONFIG'])
    config.write_bytes(PINNED)
    def popen(*args, **kwargs):
        if failure == 'launch':
            raise OSError('connector missing')
        return Tunnel(output='Host key verification failed.\n', code=255)
    monkeypatch.setattr(SERVER.subprocess, 'Popen', popen)
    run_threads_inline(monkeypatch)
    if failure == 'launch':
        with pytest.raises(RuntimeError, match='launch the tunnel'):
            adapter.connect('devbox', 'team')
    else:
        assert adapter.connect('devbox', 'team')['state'] == 'exited'
    assert config.read_bytes() == PINNED


def test_failed_reconnect_preserves_settings(adapter, monkeypatch):
    workspace = {'id': 'workspace123'}
    monkeypatch.setattr(adapter, 'workspace', lambda *args: {**row(), 'devworkspaceId': workspace['id']})
    monkeypatch.setattr(adapter, 'executable', lambda: 'connector')
    config = Path(SERVER.os.environ['DEVSPACES_SSH_CONFIG'])
    config.write_bytes(PINNED)
    tunnels = [Tunnel(), Tunnel(output='', code=1)]
    monkeypatch.setattr(SERVER.subprocess, 'Popen', lambda *args, **kwargs: tunnels.pop(0))
    run_threads_inline(monkeypatch)
    assert adapter.connect('devbox', 'team')['state'] == 'ready'
    workspace['id'] = 'replacement456'
    assert adapter.connect('devbox', 'team')['state'] == 'exited'
    assert config.read_bytes() == PINNED
