#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Write or inspect the user-only Agentic Workspace settings; standard library only.

Settings live in ~/.config/dell-mcp/devspaces.json (or DEVSPACES_CONFIG_FILE) because Codex does
not forward launch-environment variables to plugin servers. The server reads them once at start,
so restart the plugin (start a new Codex session) after a change.

    configure_devspaces.py --cluster-url https://devspaces.example.com
    configure_devspaces.py --ca-bundle /path/org-ca.pem          # private CA, verification on
    configure_devspaces.py --fetch-cluster-certificate            # self-signed cluster: pin its cert
    configure_devspaces.py --allow-insecure-tls                   # lab only, verification off
    configure_devspaces.py --no-allow-insecure-tls                # turn verification back on
    configure_devspaces.py --check
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import ssl
import sys
import tempfile
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import devspaces_settings  # noqa: E402


def read(path):
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}


def write(path, values):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # mkstemp creates the file user-only, so a setting is never briefly readable by others.
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix='.devspaces-', suffix='.tmp')
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            handle.write(json.dumps(values, indent=2) + '\n')
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def fetch_certificate(cluster_url, directory):
    """Save the certificate the cluster presents and return (path, SHA-256 fingerprint).

    Trust on first use: the fingerprint must be checked with the cluster administrator before the
    saved certificate is relied on. This works for a self-signed cluster certificate; for a
    certificate issued by a private CA, use that CA's certificate with --ca-bundle instead.
    """
    parsed = urlsplit(cluster_url)
    if parsed.scheme != 'https' or not parsed.hostname:
        raise ValueError('cluster_url must be an https origin')
    pem = ssl.get_server_certificate((parsed.hostname, parsed.port or 443), timeout=30)
    fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest().upper()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = directory / 'devspaces-cluster.pem'
    target.write_text(pem, encoding='utf-8')
    if os.name == 'posix':
        target.chmod(0o600)
    return target, ':'.join(fingerprint[i:i + 2] for i in range(0, len(fingerprint), 2))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--cluster-url')
    parser.add_argument('--connector', help='absolute path to the approved connector executable')
    parser.add_argument('--ca-bundle', help='PEM file to trust in addition to the system store')
    parser.add_argument('--fetch-cluster-certificate', action='store_true')
    insecure = parser.add_mutually_exclusive_group()
    insecure.add_argument('--allow-insecure-tls', action='store_true', help='lab only: disable verification')
    insecure.add_argument('--no-allow-insecure-tls', action='store_true')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args(argv)
    path = devspaces_settings.config_path()
    values = read(path)
    if args.check:
        effective = devspaces_settings.load()
        print(json.dumps({
            'config_path': str(path),
            'cluster_url': effective.get('DEVSPACES_CLUSTER_URL'),
            'connector_command': effective.get('DEVSPACES_CONNECTOR_COMMAND'),
            'ca_bundle': effective.get('NODE_EXTRA_CA_CERTS'),
            'tls_verification_enabled': not devspaces_settings.insecure_tls_allowed(effective),
        }, indent=2))
        return 0
    if args.cluster_url:
        if not devspaces_settings.cluster_origin(args.cluster_url):
            parser.error('--cluster-url must be a credential-free HTTPS origin')
        if devspaces_settings.cluster_origin(args.cluster_url) != devspaces_settings.cluster_origin(values.get('cluster_url', '')):
            values.pop('allow_insecure_tls', None)
            values.pop('insecure_tls_cluster', None)
        values['cluster_url'] = args.cluster_url.rstrip('/')
    if args.connector:
        values['connector_command'] = args.connector
    if args.ca_bundle:
        bundle = Path(args.ca_bundle).expanduser().resolve()
        ssl.PEM_cert_to_DER_cert(bundle.read_text(encoding='utf-8'))  # reject a file that is not PEM
        values['ca_bundle'] = str(bundle)
    if args.fetch_cluster_certificate:
        if not values.get('cluster_url'):
            parser.error('set --cluster-url first')
        target, fingerprint = fetch_certificate(values['cluster_url'], path.parent)
        values['ca_bundle'] = str(target)
        print(f'Saved the cluster certificate to {target}.')
        print(f'SHA-256 fingerprint: {fingerprint}')
        print('Confirm this fingerprint with the cluster administrator before relying on it.')
    if args.allow_insecure_tls:
        if not devspaces_settings.cluster_origin(values.get('cluster_url', '')):
            parser.error('set --cluster-url before approving a TLS exception')
        values['allow_insecure_tls'] = True
        values['insecure_tls_cluster'] = values['cluster_url']
        print('WARNING: certificate verification is disabled only for this approved lab cluster. '
              'Use --no-allow-insecure-tls to revoke the exception.')
    if args.no_allow_insecure_tls:
        values.pop('allow_insecure_tls', None)
        values.pop('insecure_tls_cluster', None)
    write(path, values)
    print(f'Wrote {path}. Restart the plugin (start a new Codex session) to apply it.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
