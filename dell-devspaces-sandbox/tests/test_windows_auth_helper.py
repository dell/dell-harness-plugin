"""Exercise the Windows launcher with a synthetic executable; never real auth."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

HELPER = Path(__file__).parents[1] / 'scripts' / 'authenticate_devspaces.ps1'
POWERSHELL = os.getenv('DEVSPACES_TEST_POWERSHELL') or shutil.which('powershell.exe')
COMPILER_HOST = shutil.which('powershell.exe')
pytestmark = pytest.mark.skipif(os.name != 'nt' or not POWERSHELL, reason='Windows PowerShell required')


@pytest.fixture(scope='module')
def connector_stub(tmp_path_factory):
    root = tmp_path_factory.mktemp('auth-helper-stub')
    executable = root / 'connector stub.exe'
    source = root / 'stub.cs'
    source.write_text('''using System;
class Stub {
  static int Main(string[] args) {
    Console.WriteLine("ARGS=" + String.Join("|", args));
    var tls = Environment.GetEnvironmentVariable("NODE_TLS_REJECT_UNAUTHORIZED");
    Console.WriteLine("TLS=" + (String.IsNullOrEmpty(tls) ? "verified" : tls));
    return Environment.GetEnvironmentVariable("DEVSPACES_TEST_FAIL") == "1" ? 7 : 0;
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
    return subprocess.run([POWERSHELL, '-NoProfile', '-File', str(HELPER),
        '-ConnectorPath', str(executable), '-ClusterUrl', origin, '-PauseOnCompletion', *extra],
        input='\n', capture_output=True, text=True, timeout=20,
        env={**os.environ, 'NODE_TLS_REJECT_UNAUTHORIZED': '0', **(env or {})})


def test_success_requires_verification_and_ignores_ambient_tls(connector_stub):
    result = run_helper(connector_stub)
    assert result.returncode == 0
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
    result = run_helper(connector_stub, extra=['-AllowInsecureTls'])
    assert result.returncode == 0
    assert 'TLS=0' in result.stdout
    assert 'Explicit TLS exception' in result.stdout


@pytest.mark.parametrize('origin', ['http://example.test', 'https://user:secret@example.test',
                                   'https://example.test/?token=secret', 'not a URL'])
def test_invalid_origin_never_invokes_connector(connector_stub, origin):
    result = run_helper(connector_stub, origin)
    assert result.returncode != 0
    assert 'ARGS=' not in result.stdout
    assert 'credential-free HTTPS cluster origin' in result.stdout
    assert 'secret' not in result.stdout + result.stderr
    assert 'Press Enter to close' in result.stdout


def test_missing_executable_stays_visible(tmp_path):
    result = run_helper(tmp_path / 'missing.exe')
    assert result.returncode != 0
    assert 'approved connector executable' in result.stdout
    assert 'Press Enter to close' in result.stdout
