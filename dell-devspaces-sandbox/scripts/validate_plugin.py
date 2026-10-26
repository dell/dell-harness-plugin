#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Offline pre-push checks, relocated package tests, and reproducible evidence.

Run with requirements-dev.txt installed. Does not claim live or agent evaluation.
"""
import argparse
import hashlib
import importlib.metadata
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def check_package(root):
    manifest = json.loads((root / '.codex-plugin/plugin.json').read_text(encoding='utf-8'))
    assert manifest['name'] == 'dell-devspaces-sandbox'
    assert re.fullmatch(r'\d+\.\d+\.\d+', manifest['version'])
    assert manifest['author']['name'] and manifest['repository'] and manifest['homepage']
    interface = manifest['interface']
    assert interface['displayName'] == 'Dell Redhat Devspaces AI Factory Skill'
    assert len(interface['defaultPrompt']) >= 3
    for value in (manifest['skills'], manifest['mcpServers'], interface['composerIcon'], interface['logo']):
        assert value.startswith('./')
        resolved = (root / value).resolve()
        assert resolved.is_relative_to(root.resolve()) and resolved.exists(), value
    config = json.loads((root / manifest['mcpServers']).read_text(encoding='utf-8'))
    server = config['mcpServers']['dell_devspaces']
    assert server['command'] == 'python'
    assert server['args'] == ['${PLUGIN_ROOT}/scripts/prepare_runtime.py']
    for path in (root / 'skills').rglob('SKILL.md'):
        text = path.read_text(encoding='utf-8')
        assert text.startswith('---\n'), 'Skill frontmatter must be BOM-free'
        meta = yaml.safe_load(text.split('---', 2)[1])
        assert meta['name'] == path.parent.name and meta['description']
        assert len(meta['name']) <= 64 and re.fullmatch(r'[a-z0-9-]+', meta['name'])
        for target in re.findall(r'\]\(([^)]+)\)', text):
            if '://' not in target and not target.startswith('#'):
                resolved = (path.parent / target.split('#')[0]).resolve()
                assert resolved.is_relative_to(root.resolve()) and resolved.exists(), target
        ui = yaml.safe_load((path.parent / 'agents/openai.yaml').read_text())
        assert '$' + meta['name'] in ui['interface']['default_prompt']
    return manifest


def runtime_files():
    singles = ['.codex-plugin/plugin.json', '.mcp.json', 'README.md', 'requirements.txt',
               'evals/cases.json', 'validation/REVIEW.md',
               'scripts/connector_mcp_server.py', 'scripts/devspaces_mcp_server.py',
               'scripts/authenticate_devspaces.py', 'scripts/authenticate_devspaces.ps1', 'scripts/launch-auth.cmd',
               'scripts/prepare_runtime.py', 'scripts/mcp_session.py']
    paths = [ROOT / name for name in singles]
    for directory in ('skills', 'assets', 'config'):
        paths.extend(p for p in (ROOT / directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts)
    return sorted(paths)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'validation/evidence')
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    manifest = check_package(ROOT)
    marketplace = json.loads((ROOT.parent / '.agents/plugins/marketplace.json').read_text())
    entry = next(item for item in marketplace['plugins'] if item['name'] == manifest['name'])
    assert (ROOT.parent / entry['source']['path']).resolve() == ROOT

    files = runtime_files()
    patterns = [r'-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----',
                r'gh[pousr]_[A-Za-z0-9]{30,}', r'(?i)bearer\s+[A-Za-z0-9._-]{30,}']
    for path in files:
        assert path.suffix not in {'.exe', '.env', '.pem', '.key'}
        if path.suffix in {'.py', '.ps1', '.cmd', '.md', '.json', '.yaml', '.txt'}:
            content = path.read_text(encoding='utf-8')
            assert not any(re.search(pattern, content) for pattern in patterns), f'Possible secret: {path.name}'

    archive = out / f"{manifest['name']}-{manifest['version']}.zip"
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as package:
        for path in files:
            package.write(path, f"{ROOT.name}/{path.relative_to(ROOT).as_posix()}")
    env = {**os.environ, 'PYTHONPATH': os.pathsep.join(sys.path)}
    with tempfile.TemporaryDirectory(prefix='devspaces-package-') as temporary:
        with zipfile.ZipFile(archive) as package:
            package.extractall(temporary)
        relocated = Path(temporary) / ROOT.name
        check_package(relocated)
        # Boot the exact packaged script outside the checkout, without credentials.
        help_result = subprocess.run([sys.executable, str(relocated / 'scripts/connector_mcp_server.py'), '--help'],
                                     env=env, capture_output=True, text=True, timeout=20)
        assert help_result.returncode == 0, help_result.stderr
        previous = {key: os.environ.get(key) for key in ('DEVSPACES_TEST_PLUGIN_ROOT', 'DEVSPACES_TEST_EVIDENCE_DIR')}
        os.environ['DEVSPACES_TEST_PLUGIN_ROOT'] = str(relocated)
        os.environ['DEVSPACES_TEST_EVIDENCE_DIR'] = str(out)
        try:
            code = pytest.main([str(ROOT / 'tests'), '-q', '-p', 'no:cacheprovider',
                                f'--rootdir={ROOT.parent}', f'--junitxml={out / "pytest.xml"}'])
        finally:
            for key, value in previous.items():
                if value is None: os.environ.pop(key, None)
                else: os.environ[key] = value
    report = ET.parse(out / 'pytest.xml')
    suite = report.getroot().find('testsuite')
    # Published evidence must not reveal the machine or checkout path that produced it.
    local_prefix = str(ROOT.parent) + os.sep
    for element in report.iter():
        element.attrib.pop('hostname', None)
        for key, value in element.attrib.items():
            element.attrib[key] = value.replace(local_prefix, '')
        for field in ('text', 'tail'):
            value = getattr(element, field)
            if value:
                setattr(element, field, value.replace(local_prefix, ''))
    report.write(out / 'pytest.xml', encoding='utf-8', xml_declaration=True)
    sources = sorted(set(files + list((ROOT / 'tests').rglob('*.py')) + [Path(__file__).resolve(), ROOT / 'requirements-dev.txt']))
    result = {
        'captured_at_utc': datetime.now(timezone.utc).isoformat(),
        'plugin_version': manifest['version'], 'python_version': sys.version.split()[0],
        'platform': sys.platform, 'evidence_type': 'offline automated checks; synthetic MCP backend',
        'passed': code == 0, 'tests': int(suite.attrib['tests']),
        'failures': int(suite.attrib['failures']), 'errors': int(suite.attrib['errors']),
        'skipped': int(suite.attrib['skipped']), 'duration_seconds': float(suite.attrib['time']),
        'package_checks': 'passed', 'relocated_entrypoint': 'passed',
        'secret_pattern_scan': 'passed; limited patterns, not a security certification',
        'archive': archive.name, 'archive_sha256': digest(archive),
        'dependencies': {name: importlib.metadata.version(name) for name in ('mcp', 'kubernetes', 'pydantic', 'pytest', 'PyYAML', 'jsonschema')},
        'source_sha256': {p.relative_to(ROOT).as_posix(): digest(p) for p in sources},
        'not_validated': ['model-driven skill selection and installed-chat workflows',
                          'live workspace lifecycle and SSH', 'public directory/ChatGPT/mobile support'],
    }
    (out / 'summary.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'source_sha256'}, indent=2))
    return int(code)


if __name__ == '__main__':
    raise SystemExit(main())
