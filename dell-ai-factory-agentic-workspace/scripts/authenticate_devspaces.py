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


def probe(connector, cluster_id, insecure=False, automatic=True, empty_retry=False):
    """Return sanitized access evidence and the effective child-only TLS setting."""
    original_insecure = insecure
    empty_diagnostic = False
    for attempt in range(2):
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
            if not rows and empty_retry and automatic and not insecure and attempt == 0:
                print('Empty CLI inventory is inconclusive. Checking once with certificate verification disabled; this can permit interception.', file=sys.stderr, flush=True)
                empty_diagnostic = True
                insecure = True
                continue
            if empty_diagnostic and not verified:
                insecure = original_insecure
            return {'list_accessible': True, 'cluster_verified': verified,
                    'workspace_count': len(rows), 'tls_verification_enabled': not insecure}, insecure
        certificate_failure = any(marker in (result.stderr or '').lower() for marker in CERTIFICATE_ERRORS)
        if certificate_failure:
            if automatic and not insecure and attempt == 0:
                insecure = True
                print('Certificate verification failed. Retrying once with verification disabled for this connector session; this can permit interception.', file=sys.stderr, flush=True)
                continue
            raise RuntimeError('Certificate verification failed; automatic retry unavailable or exhausted.')
        # No raw output: the CLI can include credential-bearing URIs in errors.
        return {'list_accessible': False, 'cluster_verified': False,
                'reason': 'CLI list failed; local authentication or connectivity needs attention.'}, insecure
    raise RuntimeError('CLI access could not be verified.')


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


def authenticate(connector, cluster, cluster_id, insecure=False, automatic=True, report=None):
    report = report or (lambda state, **fields: None)
    report('checking_access', tls_verification_enabled=not insecure)
    status, insecure = probe(connector, cluster_id, insecure, automatic, empty_retry=True)
    if status['cluster_verified']:
        print('Existing authentication verified; login skipped.', flush=True)
        print(json.dumps(status), flush=True)
        report('verified', **status)
        return 0
    print('Complete SSO in your machine browser and private authentication prompts in this terminal. Do not paste authentication URIs into chat.', flush=True)
    # Auth is not a workspace mutation. Retry only an observed certificate failure,
    # including CLI builds whose --list silently returns [] when TLS validation fails.
    for attempt in range(2):
        report('waiting_for_sso', tls_verification_enabled=not insecure)
        code, certificate_failure = interactive_auth(connector, cluster, insecure)
        if code and certificate_failure and automatic and not insecure and attempt == 0:
            insecure = True
            print('Authentication certificate verification failed. Retrying once with verification disabled for this connector session; this can permit interception.', flush=True)
            continue
        break
    if code:
        print('Authentication command failed or was interrupted; login not verified.', flush=True)
        report('failed', tls_verification_enabled=not insecure)
        return 1
    report('checking_access', tls_verification_enabled=not insecure)
    status, _ = probe(connector, cluster_id, insecure, automatic, empty_retry=True)
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
    parser.add_argument('--allow-insecure-tls', action='store_true', help='Reuse the current session TLS exception')
    parser.add_argument('--no-auto-tls-retry', action='store_true', help='Require a trusted certificate')
    args = parser.parse_args(argv)
    code = 1
    try:
        connector, cluster, cluster_id = validate(args.connector, args.cluster)
        automatic = not args.no_auto_tls_retry and os.getenv('DEVSPACES_AUTO_TLS_RETRY', 'true').lower() in {'1', 'true', 'yes'}
        if args.check:
            status, _ = probe(connector, cluster_id, args.allow_insecure_tls, automatic, empty_retry=True)
            print(json.dumps(status), flush=True)
            return 0 if status['cluster_verified'] else 2
        if not args.foreground:
            if os.name != 'nt':
                raise RuntimeError('Use --foreground in an interactive local terminal on this platform.')
            command = [sys.executable, str(Path(__file__).resolve()), '--foreground', '--pause',
                       '--connector', connector, '--cluster', cluster]
            if args.allow_insecure_tls:
                command.append('--allow-insecure-tls')
            if not automatic:
                command.append('--no-auto-tls-retry')
            if args.status_file:
                command.extend(['--status-file', str(args.status_file.resolve())])
            process = subprocess.Popen(command, creationflags=subprocess.CREATE_NEW_CONSOLE,
                                       env=child_environment(False))
            print(json.dumps({'state': 'launched', 'pid': process.pid, 'browser': 'machine-default',
                              'authentication_verified': False}), flush=True)
            return 0
        code = authenticate(connector, cluster, cluster_id, args.allow_insecure_tls, automatic,
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
