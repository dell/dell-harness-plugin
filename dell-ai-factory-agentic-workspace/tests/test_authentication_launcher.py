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


def test_bad_certificate_is_rejected_before_auth_without_downgrade(monkeypatch, capsys):
    monkeypatch.setenv('DEVSPACES_AUTO_TLS_RETRY', 'true')
    calls = []
    def run(args, **kwargs):
        calls.append((args[1], kwargs['env'].get('NODE_TLS_REJECT_UNAUTHORIZED')))
        return subprocess.CompletedProcess(args, 1, SECRET, 'unable to verify the first certificate ' + SECRET)
    monkeypatch.setattr(AUTH.subprocess, 'run', run)
    monkeypatch.setattr(AUTH, 'interactive_auth', lambda *args: pytest.fail('must not authenticate'))
    with pytest.raises(RuntimeError, match='Certificate verification failed') as exc:
        AUTH.authenticate('connector', ORIGIN, CLUSTER)
    assert calls == [('--list', None)]
    assert SECRET not in str(exc.value) + capsys.readouterr().out


def test_certificate_failure_during_sign_in_fails_closed(monkeypatch):
    seen = []
    def run(args, **kwargs):
        seen.append(kwargs['env'].get('NODE_TLS_REJECT_UNAUTHORIZED'))
        return subprocess.CompletedProcess(args, 0, '[]', '')
    monkeypatch.setattr(AUTH.subprocess, 'run', run)
    attempts = []
    def interactive(connector, cluster, insecure):
        attempts.append(insecure)
        return 1, True
    monkeypatch.setattr(AUTH, 'interactive_auth', interactive)
    reports = []
    assert AUTH.authenticate('connector', ORIGIN, CLUSTER,
                             report=lambda state, **fields: reports.append((state, fields))) == 1
    assert attempts == [False]
    assert seen == [None]
    assert reports[-1] == ('failed', {'tls_verification_enabled': True})
    assert all(fields.get('tls_verification_enabled') is True for _, fields in reports)


def test_generic_error_does_not_disable_tls(monkeypatch):
    seen = []
    def run(args, **kwargs):
        seen.append(kwargs['env'])
        return subprocess.CompletedProcess(args, 1, SECRET, '403 ' + SECRET)
    monkeypatch.setattr(AUTH.subprocess, 'run', run)
    status = AUTH.probe('connector', CLUSTER)
    assert not status['list_accessible'] and status['tls_verification_enabled']
    assert 'NODE_TLS_REJECT_UNAUTHORIZED' not in seen[0]
    assert len(seen) == 1
    assert SECRET not in json.dumps(status)


def test_explicit_opt_in_is_the_only_unverified_mode(monkeypatch):
    seen = []
    def run(args, **kwargs):
        seen.append(kwargs['env'].get('NODE_TLS_REJECT_UNAUTHORIZED'))
        return subprocess.CompletedProcess(args, 0, json.dumps([{'clusterId': CLUSTER}]), '')
    monkeypatch.setattr(AUTH.subprocess, 'run', run)
    assert AUTH.probe('connector', CLUSTER, insecure=True)['tls_verification_enabled'] is False
    assert AUTH.probe('connector', CLUSTER)['tls_verification_enabled'] is True
    assert seen == ['0', None]


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
    status = AUTH.probe('connector', CLUSTER)
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


def test_status_file_contains_no_terminal_data(tmp_path):
    path = tmp_path / 'status.json'
    AUTH.publish_status(path, ORIGIN, 'verified', cluster_verified=True, terminal=SECRET, token=SECRET)
    assert json.loads(path.read_text()) == {'state': 'verified', 'cluster_url': ORIGIN, 'cluster_verified': True}
    assert SECRET not in path.read_text()
