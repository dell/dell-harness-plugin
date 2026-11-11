# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""User-scoped settings for the Agentic Workspace plugin; standard library only.

Codex forwards only a fixed set of variables (HOME, PATH, ...) to a plugin's MCP server, so
settings exported where Codex starts never reach it. They are read from a user-only JSON file
instead (``~/.config/dell-mcp/devspaces.json`` or ``DEVSPACES_CONFIG_FILE``); a variable that
does reach the process still takes precedence. Read once at startup; nothing writes it at runtime.
"""
import json
import os
from pathlib import Path
import stat
from urllib.parse import urlsplit

KEYS = {
    'cluster_url': 'DEVSPACES_CLUSTER_URL',
    'connector_command': 'DEVSPACES_CONNECTOR_COMMAND',
    'allowed_namespaces': 'DEVSPACES_ALLOWED_NAMESPACES',
    'enable_writes': 'DEVSPACES_ENABLE_WRITES',
    'enable_delete': 'DEVSPACES_ENABLE_DELETE',
    # Trust a private or self-signed cluster certificate while keeping verification on.
    'ca_bundle': 'NODE_EXTRA_CA_CERTS',
    # Lab-only: run the connector without certificate verification.
    'allow_insecure_tls': 'DEVSPACES_ALLOW_INSECURE_TLS',
    'insecure_tls_cluster': 'DEVSPACES_INSECURE_TLS_CLUSTER',
}


def config_path(environ=os.environ):
    configured = environ.get('DEVSPACES_CONFIG_FILE')
    return Path(configured).expanduser() if configured else Path.home() / '.config' / 'dell-mcp' / 'devspaces.json'


def load(environ=os.environ):
    """The effective settings: process environment first, then the user-only file."""
    path = config_path(environ)
    values = {}
    if path.is_file():
        if os.name == 'posix':
            status = path.stat()
            if stat.S_IMODE(status.st_mode) & 0o077 or status.st_uid != os.getuid():
                raise ValueError(f'{path} must be owned by you and user-only (chmod 600)')
        document = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(document, dict):
            raise ValueError(f'{path} must contain a JSON object')
        for key, value in document.items():
            if key in KEYS and value not in (None, ''):
                values[KEYS[key]] = ','.join(value) if isinstance(value, list) else str(value).lower() if isinstance(value, bool) else str(value)
    values.update({name: environ[name] for name in KEYS.values() if environ.get(name)})
    return values


def enabled(settings, name):
    return str(settings.get(name, 'false')).lower() in {'1', 'true', 'yes'}


def cluster_origin(value):
    """Canonical HTTPS origin; malformed or credential-bearing values fail closed."""
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
            return None
        return (parsed.hostname.lower(), parsed.port or 443)
    except (ValueError, TypeError):
        return None


def insecure_tls_allowed(settings, cluster=None):
    target = cluster_origin(cluster if cluster is not None else settings.get('DEVSPACES_CLUSTER_URL', ''))
    approved = cluster_origin(settings.get('DEVSPACES_INSECURE_TLS_CLUSTER', ''))
    return enabled(settings, 'DEVSPACES_ALLOW_INSECURE_TLS') and target is not None and target == approved
