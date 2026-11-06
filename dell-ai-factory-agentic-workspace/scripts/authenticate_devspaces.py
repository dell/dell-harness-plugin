#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Native CLI authentication; standard library only, no credential logging."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from urllib.parse import urlsplit

CERTIFICATE_ERRORS = (
    'unable to verify the first certificate', 'unable to get local issuer certificate',
    'self signed certificate', 'self-signed certificate',
    'unable_to_verify_leaf_signature', 'unable_to_get_issuer_cert_locally',
)


def validate(connector, cluster):
    executable = shutil.which(connector)
    if not executable:
        raise ValueError('Provide an existing approved connector executable.')
    parsed = urlsplit(cluster)
    if (parsed.scheme != 'https' or not parsed.hostname or '@' in parsed.netloc
            or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        raise ValueError('Use a credential-free HTTPS cluster origin.')
    return str(Path(executable).resolve()), cluster.rstrip('/'), parsed.netloc


def child_environment(insecure):
    env = os.environ.copy()
    env.pop('NODE_TLS_REJECT_UNAUTHORIZED', None)
    if insecure:
        env['NODE_TLS_REJECT_UNAUTHORIZED'] = '0'
    return env


def probe(connector, cluster_id, insecure=False):
    """Return sanitized access evidence from one CLI list.

    Certificate verification is never relaxed here. Only an explicit, administrator-set
    --allow-insecure-tls (passed in as insecure) runs unverified, and a certificate failure
    is reported as a failure rather than retried.
    """
    result = subprocess.run([connector, '--list'], stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, encoding='utf-8', errors='replace',
                            timeout=30, env=child_environment(insecure))
    if result.returncode == 0:
        try:
            rows = json.loads(result.stdout)
        except (ValueError, TypeError):
            raise RuntimeError('Connector list returned invalid JSON; authentication not verified.') from None
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise RuntimeError('Connector list returned an invalid workspace list.')
        verified = bool(rows) and all(row.get('clusterId') == cluster_id for row in rows)
        return {'list_accessible': True, 'cluster_verified': verified,
                'workspace_count': len(rows), 'tls_verification_enabled': not insecure}
    if any(marker in (result.stderr or '').lower() for marker in CERTIFICATE_ERRORS):
        raise RuntimeError('Certificate verification failed; configure a trusted CA with NODE_EXTRA_CA_CERTS.')
    # No raw output: the CLI can include credential-bearing URIs in errors.
    return {'list_accessible': False, 'cluster_verified': False, 'tls_verification_enabled': not insecure,
            'reason': 'CLI list failed; local authentication or connectivity needs attention.'}


def interactive_auth(connector, cluster, insecure):
    """Keep prompts on the native console; inspect stderr only for a TLS category.

    Lines are forwarded immediately to the local console, never retained or logged.
    Do not invoke interactive mode through a tool that captures the user's terminal.
    """
    process = subprocess.Popen([connector, '--auth', cluster], env=child_environment(insecure),
                               stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace')
    certificate = threading.Event()
    def forward_errors():
        try:
            for line in process.stderr:
                if any(marker in line.lower() for marker in CERTIFICATE_ERRORS):
                    certificate.set()
                sys.stderr.write(line)
                sys.stderr.flush()
        finally:
            process.stderr.close()
    reader = threading.Thread(target=forward_errors, daemon=True)
    reader.start()
    try:
        code = process.wait()
    except KeyboardInterrupt:
        if process.poll() is None:
            process.terminate()
            process.wait()
        raise
    finally:
        reader.join()
    return code, certificate.is_set()


def authenticate(connector, cluster, cluster_id, insecure=False, report=None):
    report = report or (lambda state, **fields: None)
    report('checking_access', tls_verification_enabled=not insecure)
    status = probe(connector, cluster_id, insecure)
    if status['cluster_verified']:
        print('Existing authentication verified; login skipped.', flush=True)
        print(json.dumps(status), flush=True)
        report('verified', **status)
        return 0
    print('Complete SSO in your machine browser and private authentication prompts in this terminal. Do not paste authentication URIs into chat.', flush=True)
    report('waiting_for_sso', tls_verification_enabled=not insecure)
    code, certificate_failure = interactive_auth(connector, cluster, insecure)
    if code:
        if certificate_failure:
            print('Authentication failed certificate verification. Configure a trusted CA with NODE_EXTRA_CA_CERTS; verification stays enabled.', flush=True)
        else:
            print('Authentication command failed or was interrupted; login not verified.', flush=True)
        report('failed', tls_verification_enabled=not insecure)
        return 1
    report('checking_access', tls_verification_enabled=not insecure)
    status = probe(connector, cluster_id, insecure)
    print(json.dumps(status), flush=True)
    if status['cluster_verified']:
        print('CLI authentication and cluster verified.', flush=True)
        report('verified', **status)
        return 0
    print('Authentication command ended; this does not confirm login. Empty inventory cannot verify the cluster. Return to the agent to check account and workspace access.', flush=True)
    report('unverified', **status)
    return 2


def publish_status(path, cluster, state, **fields):
    if path is None:
        return
    # Explicit allowlist: terminal content, credentials, and raw exceptions never
    # become machine-readable status. Native-window visibility is checked separately.
    allowed = {'list_accessible', 'cluster_verified', 'workspace_count', 'tls_verification_enabled'}
    value = {'state': state, 'cluster_url': cluster,
             **{key: item for key, item in fields.items() if key in allowed}}
    target = Path(path)
    temporary = target.with_suffix('.tmp')
    try:
        temporary.write_text(json.dumps(value), encoding='utf-8')
        temporary.replace(target)
    except OSError:
        # The owner can still detect an exited helper and recheck CLI access.
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--connector', default=os.getenv('DEVSPACES_CONNECTOR_COMMAND', 'devspaces-cli-connector'))
    parser.add_argument('--cluster', default=os.getenv('DEVSPACES_CLUSTER_URL', ''))
    parser.add_argument('--foreground', action='store_true', help='Use the existing local terminal')
    parser.add_argument('--check', action='store_true', help='Read-only CLI access check; never launch authentication')
    parser.add_argument('--pause', action='store_true', help='Keep the native terminal visible after completion')
    parser.add_argument('--status-file', type=Path, help='Private sanitized progress file for the owning MCP session')
    parser.add_argument('--allow-insecure-tls', action='store_true',
                        help='Administrator opt-in mirrored from DEVSPACES_ALLOW_INSECURE_TLS')
    args = parser.parse_args(argv)
    code = 1
    try:
        connector, cluster, cluster_id = validate(args.connector, args.cluster)
        if args.check:
            status = probe(connector, cluster_id, args.allow_insecure_tls)
            print(json.dumps(status), flush=True)
            return 0 if status['cluster_verified'] else 2
        if not args.foreground:
            if os.name != 'nt':
                raise RuntimeError('Use --foreground in an interactive local terminal on this platform.')
            command = [sys.executable, str(Path(__file__).resolve()), '--foreground', '--pause',
                       '--connector', connector, '--cluster', cluster]
            if args.allow_insecure_tls:
                command.append('--allow-insecure-tls')
            if args.status_file:
                command.extend(['--status-file', str(args.status_file.resolve())])
            process = subprocess.Popen(command, creationflags=subprocess.CREATE_NEW_CONSOLE,
                                       env=child_environment(False))
            print(json.dumps({'state': 'launched', 'pid': process.pid, 'browser': 'machine-default',
                              'authentication_verified': False}), flush=True)
            return 0
        code = authenticate(connector, cluster, cluster_id, args.allow_insecure_tls,
                            report=lambda state, **fields: publish_status(args.status_file, cluster, state, **fields))
    except KeyboardInterrupt:
        publish_status(args.status_file, args.cluster, 'cancelled')
        print('Authentication interrupted; login not verified.', flush=True)
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired):
        # Invalid URL input must never be written into status.
        publish_status(args.status_file, '', 'failed')
        # Never include raw exceptions, subprocess output, or URL input in diagnostics.
        print('Unable to verify CLI authentication. Check the connector path, credential-free HTTPS cluster origin, certificate trust, and local terminal.', flush=True)
    finally:
        if args.foreground and args.pause:
            try:
                input('Press Enter to close this window. ')
            except (EOFError, KeyboardInterrupt):
                pass
    return code


if __name__ == '__main__':
    raise SystemExit(main())
