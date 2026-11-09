# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The settings helper an agent uses to apply a TLS workaround the user chose."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import stat
import subprocess
import threading

import pytest

PATH = Path(__file__).parents[1] / 'scripts' / 'configure_devspaces.py'
SPEC = importlib.util.spec_from_file_location('configure_devspaces', PATH)
HELPER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPER)
SERVER_PATH = Path(__file__).parents[1] / 'scripts' / 'connector_mcp_server.py'


@pytest.fixture
def config(tmp_path, monkeypatch):
    path = tmp_path / 'cfg' / 'devspaces.json'
    monkeypatch.setenv('DEVSPACES_CONFIG_FILE', str(path))
    for name in ('DEVSPACES_ALLOW_INSECURE_TLS', 'DEVSPACES_CLUSTER_URL', 'NODE_EXTRA_CA_CERTS'):
        monkeypatch.delenv(name, raising=False)
    return path


def load_server():
    spec = importlib.util.spec_from_file_location('connector_for_helper_test', SERVER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_insecure_opt_in_is_written_user_only_and_reaches_a_new_server(config):
    assert HELPER.main(['--cluster-url', 'https://devspaces.example.com/', '--allow-insecure-tls']) == 0
    assert json.loads(config.read_text()) == {'cluster_url': 'https://devspaces.example.com',
                                              'allow_insecure_tls': True}
    if os.name == 'posix':
        assert stat.S_IMODE(config.stat().st_mode) == 0o600
    connector = load_server().Connector()
    assert connector.insecure_tls and connector.environment()['NODE_TLS_REJECT_UNAUTHORIZED'] == '0'
    assert HELPER.main(['--no-allow-insecure-tls']) == 0
    assert not load_server().Connector().insecure_tls


def test_ca_bundle_must_be_pem(config, tmp_path):
    bad = tmp_path / 'not.pem'
    bad.write_text('nope')
    with pytest.raises(ValueError):
        HELPER.main(['--ca-bundle', str(bad)])
    assert not config.exists()


@pytest.mark.skipif(shutil.which('openssl') is None, reason='openssl CLI needed to mint a test certificate')
def test_fetch_pins_the_presented_self_signed_certificate(config, tmp_path, capsys):
    key, cert = tmp_path / 'key.pem', tmp_path / 'cert.pem'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1', '-subj', '/CN=localhost',
                    '-keyout', str(key), '-out', str(cert)], check=True, capture_output=True)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    listener = socket.create_server(('127.0.0.1', 0))
    port = listener.getsockname()[1]

    def serve():
        connection, _ = listener.accept()
        try:
            context.wrap_socket(connection, server_side=True).close()
        except (ssl.SSLError, OSError):
            pass

    threading.Thread(target=serve, daemon=True).start()
    assert HELPER.main(['--cluster-url', f'https://127.0.0.1:{port}', '--fetch-cluster-certificate']) == 0
    saved = Path(json.loads(config.read_text())['ca_bundle'])
    expected = hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert.read_text())).hexdigest().upper()
    assert ssl.PEM_cert_to_DER_cert(saved.read_text()) == ssl.PEM_cert_to_DER_cert(cert.read_text())
    assert expected[:2] + ':' + expected[2:4] in capsys.readouterr().out
    assert 'allow_insecure_tls' not in json.loads(config.read_text())
    listener.close()
