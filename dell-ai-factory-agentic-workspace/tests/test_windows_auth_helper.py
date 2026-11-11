# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Exercise the Windows launcher with a synthetic executable; never real auth."""
import os
import json
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import signal
import time

import pytest

HELPER = Path(__file__).parents[1] / 'scripts' / 'authenticate_devspaces.py'
POWERSHELL_NAMES = {'powershell.exe', 'pwsh.exe', 'pwsh'}
# Generated per run so no credential-shaped literal lives in source, and a leak check cannot
# pass by coincidence.
URL_SECRET = secrets.token_urlsafe(12)


def trusted_powershell(candidate, override=False):
    """Resolve a PowerShell host, accepting only an existing powershell/pwsh executable.

    An override from DEVSPACES_TEST_POWERSHELL must be an absolute path, so it is never
    resolved through PATH; only the built-in default name is looked up there.
    """
    if not candidate:
        return None
    if override:
        path = Path(candidate)
        if not path.is_absolute() or not path.is_file():
            raise RuntimeError('DEVSPACES_TEST_POWERSHELL must be an absolute path to an existing executable')
        resolved = str(path)
    else:
        resolved = shutil.which(candidate)
        if not resolved:
            return None  # Not installed: the module-level skip applies.
    if Path(resolved).name.lower() not in POWERSHELL_NAMES:
        raise RuntimeError('DEVSPACES_TEST_POWERSHELL must name a powershell.exe or pwsh executable')
    return os.path.abspath(resolved)


_OVERRIDE = os.getenv('DEVSPACES_TEST_POWERSHELL')
POWERSHELL = (trusted_powershell(_OVERRIDE, override=True) if _OVERRIDE
              else trusted_powershell('powershell.exe')) if os.name == 'nt' else None
COMPILER_HOST = trusted_powershell('powershell.exe') if os.name == 'nt' else None
pytestmark = pytest.mark.skipif(os.name != 'nt' or not POWERSHELL, reason='Windows PowerShell required')


@pytest.fixture(scope='module')
def connector_stub(tmp_path_factory):
    root = tmp_path_factory.mktemp('auth-helper-stub')
    executable = root / 'connector stub.exe'
    source = root / 'stub.cs'
    source.write_text('''using System;
class Stub {
  static int Main(string[] args) {
    if (args.Length > 0 && args[0] == "--list") {
      var state = Environment.GetEnvironmentVariable("DEVSPACES_TEST_STATE");
      Console.WriteLine(!String.IsNullOrEmpty(state) && System.IO.File.Exists(state)
        ? "[{\\"clusterId\\":\\"example.test\\"}]" : "[]");
      return 0;
    }
    Console.WriteLine("ARGS=" + String.Join("|", args));
    var tls = Environment.GetEnvironmentVariable("NODE_TLS_REJECT_UNAUTHORIZED");
    Console.WriteLine("TLS=" + (String.IsNullOrEmpty(tls) ? "verified" : tls));
    if (Environment.GetEnvironmentVariable("DEVSPACES_TEST_FAIL") == "1") return 7;
    var completed = Environment.GetEnvironmentVariable("DEVSPACES_TEST_STATE");
    if (!String.IsNullOrEmpty(completed)) System.IO.File.WriteAllText(completed, "completed");
    return 0;
  }
}''', encoding='utf-8')
    quote = lambda p: "'" + str(p).replace("'", "''") + "'"
    result = subprocess.run([COMPILER_HOST, '-NoProfile', '-Command',
        f"Add-Type -Path {quote(source)} -OutputAssembly {quote(executable)} -OutputType ConsoleApplication"],
        capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stderr
    assert executable.exists()
    return executable


def run_helper(executable, origin='https://example.test', extra=(), env=None):
    return subprocess.run([sys.executable, str(HELPER), '--foreground', '--pause',
        '--connector', str(executable), '--cluster', origin, *extra],
        input='\n', capture_output=True, text=True, timeout=20,
        env={**os.environ, 'NODE_TLS_REJECT_UNAUTHORIZED': '0', **(env or {})})


def test_success_requires_verification_and_ignores_ambient_tls(connector_stub):
    result = run_helper(connector_stub)
    assert result.returncode == 2
    assert 'ARGS=--auth|https://example.test' in result.stdout
    assert 'TLS=verified' in result.stdout
    assert 'does not confirm login' in result.stdout
    assert 'Press Enter to close' in result.stdout


def test_failure_stays_visible_and_returns_nonzero(connector_stub):
    result = run_helper(connector_stub, env={'DEVSPACES_TEST_FAIL': '1'})
    assert result.returncode != 0
    assert 'failed or was interrupted' in result.stdout
    assert 'Press Enter to close' in result.stdout


def test_explicit_tls_opt_in(connector_stub):
    result = run_helper(connector_stub, extra=['--allow-insecure-tls'],
                        env={'DEVSPACES_ALLOW_INSECURE_TLS': 'true',
                             'DEVSPACES_INSECURE_TLS_CLUSTER': 'https://example.test'})
    assert result.returncode == 2
    assert 'TLS=0' in result.stdout
    assert 'does not confirm login' in result.stdout


def test_unbound_tls_setting_keeps_verification(connector_stub, tmp_path):
    result = run_helper(connector_stub, extra=['--allow-insecure-tls'],
                        env={'DEVSPACES_ALLOW_INSECURE_TLS': 'true',
                             'DEVSPACES_INSECURE_TLS_CLUSTER': '',
                             'DEVSPACES_CONFIG_FILE': str(tmp_path / 'absent.json')})
    assert 'TLS=verified' in result.stdout


def test_tls_flag_without_setting_keeps_verification(connector_stub, tmp_path):
    result = run_helper(connector_stub, extra=['--allow-insecure-tls'],
                        env={'DEVSPACES_ALLOW_INSECURE_TLS': 'false',
                             'DEVSPACES_CONFIG_FILE': str(tmp_path / 'absent.json')})
    assert 'TLS=verified' in result.stdout
    assert 'verification stays enabled' in result.stdout


@pytest.mark.parametrize('origin', ['http://example.test', f'https://user:{URL_SECRET}@example.test',
                                   f'https://example.test/?token={URL_SECRET}', 'not a URL'])
def test_invalid_origin_never_invokes_connector(connector_stub, origin):
    result = run_helper(connector_stub, origin)
    assert result.returncode != 0
    assert 'ARGS=' not in result.stdout
    assert 'credential-free HTTPS cluster origin' in result.stdout
    assert URL_SECRET not in result.stdout + result.stderr
    assert 'Press Enter to close' in result.stdout


def test_missing_executable_stays_visible(tmp_path):
    result = run_helper(tmp_path / 'missing.exe')
    assert result.returncode != 0
    assert 'connector path' in result.stdout
    assert 'Press Enter to close' in result.stdout


def test_batch_launcher_runs_without_powershell_script_execution(connector_stub):
    launcher = HELPER.with_name('launch-auth.cmd')
    result = subprocess.run(['cmd.exe', '/c', str(launcher), '--connector', str(connector_stub),
                             '--cluster', 'https://example.test'],
                            input='\n\n', capture_output=True, text=True, timeout=30,
                            env={**os.environ, 'DEVSPACES_PYTHON_COMMAND': sys.executable})
    assert result.returncode == 2
    assert 'ARGS=--auth|https://example.test' in result.stdout
    assert 'does not confirm login' in result.stdout


def test_native_console_auth_publishes_verified_status_without_readback(connector_stub, tmp_path):
    status = tmp_path / 'status.json'
    result = subprocess.run([sys.executable, str(HELPER), '--connector', str(connector_stub),
                             '--cluster', 'https://example.test', '--status-file', str(status)],
                            capture_output=True, text=True, timeout=15,
                            env={**os.environ, 'DEVSPACES_TEST_STATE': str(tmp_path / 'completed')})
    assert result.returncode == 0
    launched = json.loads(result.stdout)
    pid = launched['pid']
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if status.exists():
                evidence = json.loads(status.read_text())
                if evidence['state'] in {'verified', 'failed', 'unverified'}:
                    break
            time.sleep(0.1)
        assert evidence['state'] == 'verified'
        assert evidence['cluster_verified'] is True and evidence['workspace_count'] == 1
        assert 'ARGS=' not in status.read_text() and 'Please enter' not in status.read_text()
    finally:
        # Only the synthetic native helper created by this test is terminated.
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
