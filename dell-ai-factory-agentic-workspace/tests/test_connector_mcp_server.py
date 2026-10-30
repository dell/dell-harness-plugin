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
    adapter.auto_tls_retry = False
    monkeypatch.setattr(adapter, 'executable', lambda: 'connector')
    monkeypatch.setattr(SERVER.subprocess, 'run', lambda *args, **kwargs:
                        subprocess.CompletedProcess([], 1, '', f'unable to verify the first certificate; token={SENTINEL}'))
    with pytest.raises(RuntimeError) as exc:
        adapter.run(['--list'])
    assert 'certificate verification failed' in str(exc.value)
    assert SENTINEL not in str(exc.value)


def test_certificate_retry_is_bounded_and_child_scoped(adapter, monkeypatch, capsys):
    monkeypatch.setattr(adapter, 'executable', lambda: 'connector')
    monkeypatch.setenv('NODE_TLS_REJECT_UNAUTHORIZED', '1')
    calls = []
    def run(args, **kwargs):
        calls.append(kwargs['env'].get('NODE_TLS_REJECT_UNAUTHORIZED'))
        if len(calls) == 1:
            return subprocess.CompletedProcess(args, 1, '', 'unable to verify the first certificate ' + SENTINEL)
        return subprocess.CompletedProcess(args, 0, '[]', '')
    monkeypatch.setattr(SERVER.subprocess, 'run', run)
    assert adapter.run(['--list']) == '[]'
    assert calls == [None, '0']
    assert adapter.insecure_tls
    assert SERVER.os.environ['NODE_TLS_REJECT_UNAUTHORIZED'] == '1'
    assert SENTINEL not in capsys.readouterr().err
    adapter.run(['--list'])
    assert calls == [None, '0', '0']


@pytest.mark.parametrize('args', [['--start', 'devbox'], ['--stop', 'devbox'], ['--delete', 'devbox']])
def test_certificate_error_never_replays_a_mutation(adapter, monkeypatch, args):
    calls = []
    monkeypatch.setattr(adapter, 'executable', lambda: 'connector')
    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 1, '', 'unable to verify the first certificate')
    monkeypatch.setattr(SERVER.subprocess, 'run', run)
    with pytest.raises(RuntimeError, match='certificate'):
        adapter.run(args)
    assert len(calls) == 1
    assert not adapter.insecure_tls


def test_failed_certificate_retry_stops_after_two_attempts(adapter, monkeypatch):
    calls = []
    monkeypatch.setattr(adapter, 'executable', lambda: 'connector')
    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 1, SENTINEL, 'self signed certificate ' + SENTINEL)
    monkeypatch.setattr(SERVER.subprocess, 'run', run)
    with pytest.raises(RuntimeError) as exc:
        adapter.run(['--list'])
    assert len(calls) == 2
    assert SENTINEL not in str(exc.value)


def test_tls_retry_can_be_disabled_by_configuration(monkeypatch):
    monkeypatch.setenv('DEVSPACES_AUTO_TLS_RETRY', 'false')
    assert not SERVER.Connector().auto_tls_retry


def test_empty_inventory_differential_tls_probe_recovers_session(adapter, monkeypatch):
    calls = []
    def run(args):
        calls.append(adapter.insecure_tls)
        return json.dumps([row()] if adapter.insecure_tls else [])
    monkeypatch.setattr(adapter, 'run', run)
    assert adapter.list()[0]['name'] == 'devbox'
    assert calls == [False, True] and adapter.insecure_tls


def test_auth_progress_transfers_tls_without_terminal_confirmation(adapter, monkeypatch, tmp_path):
    class Process:
        pid = 123
        def poll(self): return None
    status_file = tmp_path / 'status.json'
    status_file.write_text(json.dumps({'cluster_url': adapter.cluster, 'state': 'verified',
                                     'cluster_verified': True, 'tls_verification_enabled': False,
                                     'terminal': SENTINEL}))
    adapter.auth_job = (Process(), status_file)
    result = adapter.authentication_progress()
    assert result['state'] == 'verified' and adapter.insecure_tls
    assert SENTINEL not in json.dumps(result)


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
    assert options['creationflags'] == subprocess.CREATE_NEW_CONSOLE
    assert not any(key in options for key in ('stdin', 'stdout', 'stderr'))
    (tmp_path / 'status.json').write_text(json.dumps({'cluster_url': adapter.cluster,
                                                    'state': 'verified', 'cluster_verified': True,
                                                    'tls_verification_enabled': False}))
    assert adapter.authentication_progress()['cluster_verified']
    assert adapter.insecure_tls


def legacy_guard(name='devbox'):
    return ('# Dell Devspaces pinned trust: ' + name + '\nHost ' + name + '\n'
            '    StrictHostKeyChecking yes\n'
            '    UserKnownHostsFile "~/.ssh/known_hosts"\n'
            'Host *\n# End Dell Devspaces pinned trust: ' + name + '\n')


@pytest.mark.parametrize('newline,bom', [(b'\n', b''), (b'\r\n', b''), (b'\r\n', b'\xef\xbb\xbf')])
def test_migration_preserves_includes_user_policy_and_other_aliases(tmp_path, newline, bom):
    path = tmp_path / 'config'
    original = ('# User settings\nHost devbox\n  StrictHostKeyChecking ask\n'
                'Include devspaces.conf\nInclude devspaces.conf\n'
                + legacy_guard('other') + 'Host other\n  Port 2222\n')
    remaining = bom + original.encode().replace(b'\n', newline)
    path.write_bytes(bom + legacy_guard().encode().replace(b'\n', newline) + remaining[len(bom):])
    SERVER.remove_legacy_ssh_trust(path, 'devbox')
    assert path.read_bytes() == remaining
    SERVER.remove_legacy_ssh_trust(path, 'devbox')
    assert path.read_bytes() == remaining


def test_migration_removes_repeated_owned_blocks(tmp_path):
    path = tmp_path / 'config'
    path.write_text(legacy_guard() + legacy_guard() + 'Include devspaces.conf\n')
    SERVER.remove_legacy_ssh_trust(path, 'devbox')
    assert path.read_text() == 'Include devspaces.conf\n'


@pytest.mark.parametrize('content', ['', 'Host devbox\n StrictHostKeyChecking yes\n',
                                    'Include devspaces.conf\n' + legacy_guard('other')])
def test_no_guard_never_rewrites_config(tmp_path, monkeypatch, content):
    path = tmp_path / 'config'
    path.write_text(content)
    monkeypatch.setattr(SERVER.tempfile, 'NamedTemporaryFile', lambda **kw: pytest.fail('unexpected rewrite'))
    SERVER.remove_legacy_ssh_trust(path, 'devbox')
    assert path.read_text() == content
    SERVER.remove_legacy_ssh_trust(tmp_path / 'missing', 'devbox')
    assert not (tmp_path / 'missing').exists()


@pytest.mark.parametrize('content', [
    legacy_guard().replace('# End Dell Devspaces pinned trust: devbox\n', ''),
    '# End Dell Devspaces pinned trust: devbox\n',
    legacy_guard().replace('Host *\n', 'Host other\n'),
    legacy_guard().replace('    StrictHostKeyChecking yes\n', '    ProxyCommand user-command\n'),
    legacy_guard() + '# Dell Devspaces pinned trust: devbox\n',
])
def test_migration_refuses_incomplete_or_modified_blocks_without_partial_write(tmp_path, content):
    path = tmp_path / 'config'
    path.write_text(content)
    original = path.read_bytes()
    with pytest.raises(RuntimeError, match='Legacy SSH guard'):
        SERVER.remove_legacy_ssh_trust(path, 'devbox')
    assert path.read_bytes() == original


def test_connect_migrates_before_cli_and_status_never_rewrites_config(adapter, monkeypatch):
    monkeypatch.setattr(adapter, 'workspace', lambda *args: row())
    monkeypatch.setattr(adapter, 'executable', lambda: 'connector')
    adapter.ssh_config.write_text(legacy_guard() + 'Include devspaces.conf\n')
    generated = 'Include devspaces.conf\nHost devbox\n Port 45678\n StrictHostKeyChecking no\n UserKnownHostsFile nul\n'
    class Process:
        pid = 123
        stdout = io.StringIO('Connection setup completed!')
        def poll(self): return None
        def terminate(self): pass
        def wait(self, timeout): pass
    def popen(*args, **kwargs):
        assert adapter.ssh_config.read_text() == 'Include devspaces.conf\n'
        adapter.ssh_config.write_text(generated)
        return Process()
    monkeypatch.setattr(SERVER.subprocess, 'Popen', popen)
    targets = []
    class Thread:
        def __init__(self, target, daemon): self.target = target
        def start(self): targets.append(self.target)
    monkeypatch.setattr(SERVER.threading, 'Thread', Thread)
    assert adapter.connect('devbox', 'team')['state'] == 'connecting'
    assert len(targets) == 1
    targets[0]()  # Readiness marker without a newline still works.
    monkeypatch.setattr(SERVER, 'remove_legacy_ssh_trust', lambda *args: pytest.fail('unexpected migration'))
    assert adapter.connection_status('devbox', 'team')['state'] == 'ready'
    adapter.disconnect('devbox', 'team')
    assert adapter.ssh_config.read_text() == generated


def test_migration_failure_prevents_connector_launch(adapter, monkeypatch):
    monkeypatch.setattr(adapter, 'workspace', lambda *args: row())
    monkeypatch.setattr(SERVER.subprocess, 'Popen', lambda *args, **kw: pytest.fail('must migrate before launch'))
    adapter.ssh_config.write_text('# Dell Devspaces pinned trust: devbox\n')
    with pytest.raises(RuntimeError, match='Legacy SSH guard'):
        adapter.connect('devbox', 'team')
