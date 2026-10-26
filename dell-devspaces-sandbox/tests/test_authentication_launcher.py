# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Authentication behavior with synthetic CLI results; no real login or network."""
import importlib.util
import io
import json
import os
from pathlib import Path
import secrets
import subprocess

import pytest

PATH = Path(__file__).parents[1] / 'scripts/authenticate_devspaces.py'
SPEC = importlib.util.spec_from_file_location('auth_launcher', PATH)
AUTH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUTH)
SECRET = secrets.token_urlsafe(12)
ORIGIN = 'https://devspaces.example.test'
CLUSTER = 'devspaces.example.test'


def test_existing_access_skips_auth(monkeypatch, capsys):
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        assert kwargs['stdin'] == subprocess.DEVNULL
        return subprocess.CompletedProcess(args, 0, json.dumps([{'clusterId': CLUSTER}]), '')
    monkeypatch.setattr(AUTH.subprocess, 'run', run)
    assert AUTH.authenticate('connector', ORIGIN, CLUSTER) == 0
    assert calls == [['connector', '--list']]
    assert 'login skipped' in capsys.readouterr().out


def test_auth_is_interactive_and_post_checked(monkeypatch):
    calls = []
    def run(args, **kwargs):
        calls.append(args[1])
        return subprocess.CompletedProcess(args, 0, json.dumps([] if len(calls) == 1 else [{'clusterId': CLUSTER}]), '')
    monkeypatch.setattr(AUTH.subprocess, 'run', run)
    def interactive(connector, cluster, insecure):
        assert connector == 'connector' and cluster == ORIGIN and not insecure
        calls.append('--auth')
        return 0, False
    monkeypatch.setattr(AUTH, 'interactive_auth', interactive)
    assert AUTH.authenticate('connector', ORIGIN, CLUSTER) == 0
    assert calls == ['--list', '--auth', '--list']


def test_empty_inventory_does_not_claim_authentication(monkeypatch, capsys):
    monkeypatch.setattr(AUTH.subprocess, 'run', lambda args, **kwargs:
                        subprocess.CompletedProcess(args, 0, '[]', ''))
    monkeypatch.setattr(AUTH, 'interactive_auth', lambda *args: (0, False))
    assert AUTH.authenticate('connector', ORIGIN, CLUSTER) == 2
    assert 'does not confirm login' in capsys.readouterr().out


def test_certificate_retry_precedes_auth_and_preserves_parent(monkeypatch, capsys):
    monkeypatch.setenv('NODE_TLS_REJECT_UNAUTHORIZED', '1')
    calls = []
    def run(args, **kwargs):
        calls.append((args[1], kwargs['env'].get('NODE_TLS_REJECT_UNAUTHORIZED')))
        if len(calls) == 1:
            return subprocess.CompletedProcess(args, 1, SECRET, 'unable to verify the first certificate ' + SECRET)
        return subprocess.CompletedProcess(args, 0, '[]' if len(calls) == 2 else json.dumps([{'clusterId': CLUSTER}]), '')
    monkeypatch.setattr(AUTH.subprocess, 'run', run)
    def interactive(connector, cluster, insecure):
        calls.append(('--auth', '0' if insecure else None))
        return 0, False
    monkeypatch.setattr(AUTH, 'interactive_auth', interactive)
    assert AUTH.authenticate('connector', ORIGIN, CLUSTER) == 0
    assert calls == [('--list', None), ('--list', '0'), ('--auth', '0'), ('--list', '0')]
    assert os.environ['NODE_TLS_REJECT_UNAUTHORIZED'] == '1'
    assert SECRET not in capsys.readouterr().out


@pytest.mark.parametrize('automatic,count', [(True, 2), (False, 1)])
def test_certificate_failure_stops_without_auth(monkeypatch, automatic, count):
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 1, SECRET, 'self signed certificate ' + SECRET)
    monkeypatch.setattr(AUTH.subprocess, 'run', run)
    with pytest.raises(RuntimeError, match='Certificate') as exc:
        AUTH.authenticate('connector', ORIGIN, CLUSTER, automatic=automatic)
    assert len(calls) == count
    assert all(call[1] == '--list' for call in calls)
    assert SECRET not in str(exc.value)


def test_generic_error_does_not_disable_tls(monkeypatch):
    seen = []
    def run(args, **kwargs):
        seen.append(kwargs['env'])
        return subprocess.CompletedProcess(args, 1, SECRET, '403 ' + SECRET)
    monkeypatch.setattr(AUTH.subprocess, 'run', run)
    status, insecure = AUTH.probe('connector', CLUSTER)
    assert not insecure and not status['list_accessible']
    assert 'NODE_TLS_REJECT_UNAUTHORIZED' not in seen[0]
    assert len(seen) == 1
    assert SECRET not in json.dumps(status)


@pytest.mark.parametrize('cluster', ['http://example.test', 'https://example.test/path',
                                    f'https://user:{SECRET}@example.test', f'https://example.test/?token={SECRET}'])
def test_invalid_origin_rejected_without_launch(monkeypatch, cluster):
    monkeypatch.setattr(AUTH.shutil, 'which', lambda value: '/connector')
    with pytest.raises(ValueError, match='credential-free') as exc:
        AUTH.validate('connector', cluster)
    assert SECRET not in str(exc.value)


def test_wrong_cluster_never_verifies(monkeypatch):
    monkeypatch.setattr(AUTH.subprocess, 'run', lambda args, **kwargs:
                        subprocess.CompletedProcess(args, 0, '[{"clusterId":"other.example.test"}]', ''))
    status, _ = AUTH.probe('connector', CLUSTER)
    assert not status['cluster_verified']


def test_timeout_redacted_at_entrypoint(monkeypatch, capsys):
    monkeypatch.setattr(AUTH, 'validate', lambda *args: ('connector', ORIGIN, CLUSTER))
    def fail(*args, **kwargs):
        raise subprocess.TimeoutExpired(SECRET, 30, output=SECRET)
    monkeypatch.setattr(AUTH.subprocess, 'run', fail)
    assert AUTH.main(['--foreground']) == 1
    assert SECRET not in capsys.readouterr().out


def test_read_only_check_never_authenticates_or_opens_console(monkeypatch, capsys):
    monkeypatch.setattr(AUTH, 'validate', lambda *args: ('connector', ORIGIN, CLUSTER))
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, '[]', '')
    monkeypatch.setattr(AUTH.subprocess, 'run', run)
    monkeypatch.setattr(AUTH.subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('must not launch'))
    assert AUTH.main(['--check']) == 2
    assert calls == [['connector', '--list']]
    assert json.loads(capsys.readouterr().out)['cluster_verified'] is False


def test_native_launch_reports_only_launch(monkeypatch, tmp_path, capsys):
    if os.name != 'nt':
        pytest.skip('Windows console flags required')
    monkeypatch.setattr(AUTH, 'validate', lambda *args: ('connector', ORIGIN, CLUSTER))
    calls = []
    def popen(args, **kwargs):
        calls.append((args, kwargs))
        return type('Process', (), {'pid': 123})()
    monkeypatch.setattr(AUTH.subprocess, 'Popen', popen)
    assert AUTH.main([]) == 0
    args, options = calls[0]
    assert '--foreground' in args and '--pause' in args
    assert options['creationflags'] == subprocess.CREATE_NEW_CONSOLE
    assert not any(key in options for key in ('stdin', 'stdout', 'stderr'))
    status = json.loads(capsys.readouterr().out)
    assert status['state'] == 'launched' and status['authentication_verified'] is False
    assert status['browser'] == 'machine-default'


def test_interactive_auth_preserves_private_console_and_only_classifies_stderr(monkeypatch, capsys):
    calls = []
    class Process:
        stderr = io.StringIO('unable to verify the first certificate\n')
        def wait(self): return 1
    def popen(args, **kwargs):
        calls.append(args)
        assert args == ['connector', '--auth', ORIGIN]
        assert kwargs['stderr'] == subprocess.PIPE
        assert not any(key in kwargs for key in ('stdin', 'stdout', 'input'))
        return Process()
    monkeypatch.setattr(AUTH.subprocess, 'Popen', popen)
    assert AUTH.interactive_auth('connector', ORIGIN, False) == (1, True)
    assert len(calls) == 1
    assert 'unable to verify' in capsys.readouterr().err


@pytest.mark.parametrize('certificate,automatic,expected', [(True, True, [False, True]),
                                                         (False, True, [False]),
                                                         (True, False, [False])])
def test_auth_only_certificate_retry_is_bounded(monkeypatch, certificate, automatic, expected):
    monkeypatch.setattr(AUTH, 'probe', lambda *args: ({'cluster_verified': False}, args[2]))
    calls = []
    def interactive(connector, cluster, insecure):
        calls.append(insecure)
        return 1, certificate
    monkeypatch.setattr(AUTH, 'interactive_auth', interactive)
    assert AUTH.authenticate('connector', ORIGIN, CLUSTER, automatic=automatic) == 1
    assert calls == expected
