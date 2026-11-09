# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

PATH = Path(__file__).parents[1] / 'scripts/prepare_runtime.py'
SPEC = importlib.util.spec_from_file_location('runtime_bootstrap', PATH)
BOOT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BOOT)


def test_existing_mcp_runtime_is_reused(monkeypatch):
    monkeypatch.setattr(BOOT, 'usable', lambda python: True)
    monkeypatch.setattr(BOOT.subprocess, 'run', lambda *args, **kwargs: pytest.fail('must not install'))
    assert BOOT.prepare() == Path(sys.executable)


def test_missing_mcp_prepares_isolated_runtime_and_reuses_it(monkeypatch, tmp_path):
    monkeypatch.setenv('DEVSPACES_RUNTIME_DIR', str(tmp_path / 'cache'))
    ready = set()
    calls = []
    monkeypatch.setattr(BOOT, 'usable', lambda python: str(python) in ready)
    def run(command, **kwargs):
        calls.append(command)
        assert kwargs['stdout'] is sys.stderr and kwargs['stderr'] is sys.stderr
        if 'install' in command:
            ready.add(command[0])
    monkeypatch.setattr(BOOT.subprocess, 'run', run)
    python = BOOT.prepare()
    assert python.is_relative_to(tmp_path / 'cache')
    assert calls[0][1:3] == ['-m', 'venv']
    assert calls[1][-1] == 'mcp==1.30.0'
    assert not list((tmp_path / 'cache').glob('*.lock'))
    assert BOOT.prepare() == python
    assert len(calls) == 2


def test_failed_dependency_install_clears_owned_lock(monkeypatch, tmp_path):
    monkeypatch.setenv('DEVSPACES_RUNTIME_DIR', str(tmp_path))
    monkeypatch.setattr(BOOT, 'usable', lambda python: False)
    def run(command, **kwargs):
        raise subprocess.CalledProcessError(1, command)
    monkeypatch.setattr(BOOT.subprocess, 'run', run)
    with pytest.raises(subprocess.CalledProcessError):
        BOOT.prepare()
    assert not list(tmp_path.glob('*.lock'))


def test_import_check_covers_windows_stdio_and_exact_mcp_version(monkeypatch):
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        assert kwargs['stdout'] == subprocess.DEVNULL
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(BOOT.subprocess, 'run', run)
    assert BOOT.usable('python')
    assert 'mcp.client.stdio' in calls[0][-1] and '1.30.0' in calls[0][-1]


def test_parallel_bootstrap_reuses_other_owned_runtime_without_removing_lock(monkeypatch, tmp_path):
    monkeypatch.setenv('DEVSPACES_RUNTIME_DIR', str(tmp_path))
    identity = BOOT.hashlib.sha256((sys.executable + sys.version + BOOT.REQUIREMENT).encode()).hexdigest()[:16]
    lock = (tmp_path / identity).with_suffix('.lock')
    lock.mkdir()
    checks = iter([False, False, True])
    monkeypatch.setattr(BOOT, 'usable', lambda python: next(checks))
    monkeypatch.setattr(BOOT.subprocess, 'run', lambda *args, **kwargs: pytest.fail('must not reinstall'))
    assert BOOT.prepare().is_relative_to(tmp_path)
    assert lock.exists()


def test_bundled_package_never_installs_at_runtime(tmp_path, monkeypatch):
    import importlib.util
    path = Path(__file__).parents[1] / 'scripts' / 'prepare_runtime.py'
    spec = importlib.util.spec_from_file_location('prepare_runtime_bundled', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    (tmp_path / 'BUNDLE.json').write_text('{}')
    monkeypatch.setattr(module, 'usable', lambda python: False)
    monkeypatch.setattr(module.subprocess, 'run', lambda *a, **k: pytest.fail('bundle must not install'))
    with pytest.raises(RuntimeError, match='reinstall'):
        module.prepare()
