#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Dell Inc. or its subsidiaries. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Dell Redhat Devspaces AI Factory Skill: local CLI connector MCP adapter."""
import argparse
import atexit
import json
import os
import re
import shutil
import subprocess
import threading
import time
from urllib.parse import urlparse
from typing import Annotated, Literal
from typing_extensions import TypedDict, NotRequired

from pydantic import Field

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

Name = Annotated[str, Field(min_length=1, max_length=253, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$", description="Exact name from list_devspaces; no URI or CLI option")]
Identity = Annotated[str, Field(min_length=1, max_length=253, description="devworkspaceId from the most recent status/list; prevents acting on a replacement workspace")]
Timeout = Annotated[int, Field(ge=1, le=300, description="Maximum wait per state transition in seconds, excluding individual CLI command time")]


class Workspace(TypedDict):
    name: str
    namespace: str
    clusterId: str
    devworkspaceId: str
    phase: str
    started: bool
    displayName: str | None
    creationTimestamp: str | None


class Connection(TypedDict):
    name: str
    namespace: str
    state: Literal['not_managed', 'connecting', 'ready', 'exited']
    pid: NotRequired[int | None]
    exit_code: NotRequired[int | None]
    ssh_alias: NotRequired[str | None]


class Status(Workspace):
    connection: Connection


class WorkspacePage(TypedDict):
    workspaces: list[Workspace]
    total: int
    next_offset: int | None


class Runtime(TypedDict):
    backend: str
    command: str
    cluster_url: str
    version: str
    writes_enabled: bool
    delete_enabled: bool
    tls_verification_enabled: bool
    allowed_namespaces: list[str]
    scope: str


class AuthInstructions(TypedDict):
    executable: str
    arguments: list[str]
    interactive_required: bool
    instructions: str
    environment: dict[str, str]


class AuthStatus(TypedDict):
    list_accessible: bool
    cluster_verified: bool
    workspace_count: NotRequired[int | None]
    reason: NotRequired[str | None]


class DeleteResult(TypedDict):
    deletion_requested: bool
    name: str
    namespace: str


def enabled(name):
    return os.getenv(name, "false").lower() in {"1", "true", "yes"}


class Connector:
    def __init__(self):
        self.command = os.getenv("DEVSPACES_CONNECTOR_COMMAND", "devspaces-cli-connector")
        # No built-in default: each deployment supplies its own Dev Spaces origin.
        self.cluster = os.getenv("DEVSPACES_CLUSTER_URL", "").strip().rstrip("/")
        self.cluster_id = ""
        if self.cluster:
            parsed = urlparse(self.cluster)
            if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                    or parsed.password or parsed.query or parsed.fragment or parsed.path):
                raise ValueError("DEVSPACES_CLUSTER_URL must be a credential-free HTTPS origin")
            self.cluster_id = parsed.netloc
        self.namespaces = tuple(x.strip() for x in os.getenv("DEVSPACES_ALLOWED_NAMESPACES", "").split(",") if x.strip())
        if "*" in self.namespaces:
            raise ValueError("Wildcard namespaces are not supported")
        self.writes = enabled("DEVSPACES_ENABLE_WRITES")
        self.deletion = enabled("DEVSPACES_ENABLE_DELETE")
        self.insecure_tls = enabled("DEVSPACES_ALLOW_INSECURE_TLS")
        self.connections = {}
        self.lock = threading.RLock()

    def executable(self):
        path = shutil.which(self.command)
        if not path:
            raise RuntimeError("Install the approved connector and set DEVSPACES_CONNECTOR_COMMAND to its executable path")
        return path

    def environment(self):
        env = os.environ.copy()
        # Ignore ambient bypasses; require explicit plugin opt-in for this process.
        env.pop("NODE_TLS_REJECT_UNAUTHORIZED", None)
        if self.insecure_tls:
            env["NODE_TLS_REJECT_UNAUTHORIZED"] = "0"
        return env

    def options(self):
        return {"env": self.environment(), "creationflags": subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0}

    def run(self, args, timeout=30):
        try:
            result = subprocess.run([self.executable(), *args], stdin=subprocess.DEVNULL,
                                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                                    timeout=timeout, **self.options())
        except subprocess.TimeoutExpired:
            raise RuntimeError("Connector timed out; recheck status before retrying a mutation") from None
        except OSError:
            raise RuntimeError("Unable to launch the connector; verify executable permissions and platform compatibility") from None
        if result.returncode:
            # CLI errors may contain authentication URIs; do not return raw output.
            if any(marker in (result.stderr or '').lower() for marker in (
                    'unable to verify the first certificate', 'unable to get local issuer certificate',
                    'self signed certificate', 'self-signed certificate',
                    'unable_to_verify_leaf_signature', 'unable_to_get_issuer_cert_locally')):
                raise RuntimeError("Connector certificate verification failed; configure a trusted CA or use an explicitly authorized, process-scoped TLS retry")
            raise RuntimeError(f"Connector {args[0]} failed (exit {result.returncode}); check local authentication, CA trust, and connectivity")
        return result.stdout

    def require_cluster(self):
        if not self.cluster:
            raise RuntimeError("Set DEVSPACES_CLUSTER_URL to your Dev Spaces HTTPS origin before using the connector")
        return self.cluster

    def list(self, filtered=True):
        self.require_cluster()
        try:
            rows = json.loads(self.run(["--list"]))
        except json.JSONDecodeError:
            raise RuntimeError("Connector --list did not return a JSON array") from None
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise RuntimeError("Connector --list returned an invalid workspace list")
        for row in rows:
            if (any(not isinstance(row.get(key), str) or not row[key] or len(row[key]) > 253
                    for key in ('name', 'namespace', 'clusterId', 'devworkspaceId', 'phase'))
                    or type(row.get('started')) is not bool
                    or any(row.get(key) is not None and (not isinstance(row[key], str) or len(row[key]) > 512)
                           for key in ('displayName', 'creationTimestamp'))):
                raise RuntimeError("Connector workspace fields are missing or invalid; verify the installed connector version")
        # Never silently act against a session authenticated to another cluster.
        if any(row.get("clusterId") != self.cluster_id for row in rows):
            raise RuntimeError("Connector session cluster does not match DEVSPACES_CLUSTER_URL; authenticate to the intended cluster")
        fields = ("name", "displayName", "namespace", "phase", "devworkspaceId", "clusterId", "started", "creationTimestamp")
        return [{key: row.get(key) for key in fields} for row in rows
                if not filtered or not self.namespaces or row.get("namespace") in self.namespaces]

    def workspace(self, name, namespace):
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]*", name):
            raise ValueError("Use an exact workspace name, not an option or connection URI")
        if self.namespaces and namespace not in self.namespaces:
            raise PermissionError("Namespace is outside DEVSPACES_ALLOWED_NAMESPACES")
        rows = self.list(filtered=False)
        # The CLI accepts only name, so namespace cannot disambiguate duplicates.
        matches = [row for row in rows if row["name"] == name]
        if len(matches) != 1 or matches[0]["namespace"] != namespace:
            raise ValueError("Workspace must uniquely match name and namespace in the authenticated account")
        return matches[0]

    @staticmethod
    def check_identity(row, expected_identity):
        if expected_identity is not None and row['devworkspaceId'] != expected_identity:
            raise RuntimeError("Workspace identity changed; refresh status before requesting the action again")

    def wait(self, name, namespace, identity, phase, timeout):
        deadline = time.monotonic() + timeout
        while True:
            row = self.workspace(name, namespace)
            if row["devworkspaceId"] != identity:
                raise RuntimeError("Workspace identity changed; no further action was taken")
            if row["phase"] == phase and row["started"] is (phase == "Running"):
                return row
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Workspace did not reach {phase}; inspect status before retrying")
            time.sleep(min(2, max(0, deadline - time.monotonic())))

    def lifecycle(self, name, namespace, action, timeout=120, expected_identity=None):
        if not self.writes:
            raise PermissionError("Set DEVSPACES_ENABLE_WRITES=true to enable lifecycle operations")
        if action not in {"start", "stop", "restart"} or not 1 <= timeout <= 300:
            raise ValueError("Invalid lifecycle action or timeout (1-300 seconds)")
        with self.lock:
            row = self.workspace(name, namespace)
            self.check_identity(row, expected_identity)
            identity = row["devworkspaceId"]
            if not identity:
                raise RuntimeError("Workspace identity is missing")
            if action in {"stop", "restart"}:
                if row["phase"] != "Stopped" or row["started"] is not False:
                    self.run(["--stop", name])
                    row = self.wait(name, namespace, identity, "Stopped", timeout)
                self.disconnect(name, namespace)
            if action in {"start", "restart"}:
                if row["phase"] != "Running" or row["started"] is not True:
                    self.run(["--start", name])
                    row = self.wait(name, namespace, identity, "Running", timeout)
            return row

    def connection_status(self, name, namespace):
        with self.lock:
            entry = self.connections.get((namespace, name))
            if not entry:
                return {"state": "not_managed", "name": name, "namespace": namespace}
            code = entry["process"].poll()
            return {"name": name, "namespace": namespace,
                    "state": "exited" if code is not None else ("ready" if entry["ready"] else "connecting"),
                    "pid": entry["process"].pid, "exit_code": code,
                    "ssh_alias": name if code is None and entry["ready"] else None}

    def connect(self, name, namespace, expected_identity=None):
        with self.lock:
            row = self.workspace(name, namespace)
            self.check_identity(row, expected_identity)
            if row["phase"] != "Running" or row["started"] is not True:
                raise RuntimeError("Start the workspace and verify Running before connecting")
            state = self.connection_status(name, namespace)
            if state["state"] in {"ready", "connecting"}:
                if self.connections[(namespace, name)]['workspace_id'] == row['devworkspaceId']:
                    return state
                self.disconnect(name, namespace)
            try:
                process = subprocess.Popen([self.executable(), "--connect", name],
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                           **self.options())
            except OSError:
                raise RuntimeError("Unable to launch the connector tunnel; verify executable permissions and platform compatibility") from None
            entry = {"process": process, "ready": False, "workspace_id": row['devworkspaceId']}
            self.connections[(namespace, name)] = entry

            def consume():
                # Drain continuously without storing or returning potentially sensitive output.
                try:
                    for line in process.stdout:
                        if "Connection setup completed!" in line:
                            entry["ready"] = True
                finally:
                    process.stdout.close()

            threading.Thread(target=consume, daemon=True).start()
            return self.connection_status(name, namespace)

    def disconnect(self, name, namespace):
        with self.lock:
            entry = self.connections.get((namespace, name))
            if entry and entry["process"].poll() is None:
                entry["process"].terminate()
                try:
                    entry["process"].wait(timeout=5)
                except subprocess.TimeoutExpired:
                    entry["process"].kill()
                    entry["process"].wait(timeout=5)
            self.connections.pop((namespace, name), None)
            return {"state": "not_managed", "name": name, "namespace": namespace}

    def close(self):
        for namespace, name in list(self.connections):
            self.disconnect(name, namespace)


connector = Connector()
atexit.register(connector.close)
mcp = FastMCP("Dell Redhat Devspaces AI Factory Skill")
READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False)
RESTART = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False)


@mcp.tool(annotations=READ)
def get_runtime_configuration() -> Runtime:
    """Show connector configuration and available commands without reading credentials."""
    return {"backend": "cli-connector", "command": connector.executable(), "cluster_url": connector.cluster,
            "version": connector.run(["--version"]).strip(), "writes_enabled": connector.writes,
            "tls_verification_enabled": not connector.insecure_tls,
            "delete_enabled": connector.deletion, "allowed_namespaces": list(connector.namespaces),
            "scope": "Authenticated connector account; optional namespace filter"}


@mcp.tool(annotations=READ)
def get_devspaces_authentication_instructions() -> AuthInstructions:
    """Return interactive local authentication instructions; never accept or capture the workspace URI."""
    return {"executable": connector.executable(), "arguments": ["--auth", connector.require_cluster()],
            "environment": {"NODE_TLS_REJECT_UNAUTHORIZED": "0"} if connector.insecure_tls else {},
            "interactive_required": True,
            "instructions": "Run in a local terminal and complete browser/Developer Workspace URI prompts there. Do not paste the URI into chat. Then call get_authentication_status."}


@mcp.tool(annotations=READ)
def get_authentication_status() -> AuthStatus:
    """Verify that listing works; empty lists cannot prove the selected cluster."""
    try:
        rows = connector.list()
        return {"list_accessible": True, "cluster_verified": bool(rows), "workspace_count": len(rows)}
    except RuntimeError as exc:
        return {"list_accessible": False, "cluster_verified": False, "reason": str(exc)}


@mcp.tool(annotations=READ)
def list_devspaces(
    namespace: Name | None = None,
    name: Name | None = None,
    limit: Annotated[int, Field(ge=1, le=100, description="Workspaces per page")] = 25,
    offset: Annotated[int, Field(ge=0, description="Use next_offset for subsequent pages")] = 0,
) -> WorkspacePage:
    """List a bounded workspace page with stable IDs and states. Filter by exact namespace/name; follow next_offset until null. Pages reflect live inventory, not a frozen snapshot."""
    if not 1 <= limit <= 100 or offset < 0:
        raise ValueError("limit must be 1-100 and offset nonnegative")
    rows = sorted((row for row in connector.list()
                   if (namespace is None or row['namespace'] == namespace)
                   and (name is None or row['name'] == name)),
                  key=lambda row: (row['namespace'], row['name'], row['devworkspaceId']))
    end = offset + limit
    return {'workspaces': rows[offset:end], 'total': len(rows), 'next_offset': end if end < len(rows) else None}


@mcp.tool(annotations=READ)
def get_devspace_status(namespace: Name, name: Name) -> Status:
    """Return observed phase and desired started state, plus separate local tunnel state."""
    return {**connector.workspace(name, namespace), "connection": connector.connection_status(name, namespace)}


@mcp.tool(annotations=WRITE)
def start_devspace(namespace: Name, name: Name, expected_workspace_id: Identity, timeout: Timeout = 120) -> Workspace:
    """Start the exact workspace and wait up to timeout seconds for Running."""
    return connector.lifecycle(name, namespace, "start", timeout, expected_workspace_id)


@mcp.tool(annotations=DESTRUCTIVE)
def stop_devspace(namespace: Name, name: Name, expected_workspace_id: Identity, timeout: Timeout = 120) -> Workspace:
    """Stop the exact workspace and wait for Stopped; interrupts workspace processes."""
    return connector.lifecycle(name, namespace, "stop", timeout, expected_workspace_id)


@mcp.tool(annotations=RESTART)
def restart_devspace(namespace: Name, name: Name, expected_workspace_id: Identity, timeout: Timeout = 120) -> Workspace:
    """Stop, verify Stopped, then start and verify Running; timeout applies to each transition."""
    return connector.lifecycle(name, namespace, "restart", timeout, expected_workspace_id)


@mcp.tool(annotations=DESTRUCTIVE)
def connect_devspace(namespace: Name, name: Name, expected_workspace_id: Identity) -> Connection:
    """Launch a managed SSH tunnel for a Running workspace; may update local SSH config."""
    return connector.connect(name, namespace, expected_workspace_id)


@mcp.tool(annotations=READ)
def get_connection_status(namespace: Name, name: Name) -> Connection:
    """Check this MCP server's tunnel process; ready is not an end-to-end SSH probe."""
    return connector.connection_status(name, namespace)


@mcp.tool(annotations=DESTRUCTIVE)
def disconnect_devspace(namespace: Name, name: Name) -> Connection:
    """Terminate only the tunnel owned by this MCP server; leave the workspace running."""
    return connector.disconnect(name, namespace)


@mcp.tool(annotations=RESTART)
def delete_devspace(namespace: Name, name: Name, confirm_name: Name, expected_workspace_id: Identity) -> DeleteResult:
    """Request deletion of an exact workspace; requires both write gates and confirmation."""
    if not connector.writes or not connector.deletion:
        raise PermissionError("Deletion requires DEVSPACES_ENABLE_WRITES=true and DEVSPACES_ENABLE_DELETE=true")
    if confirm_name != name:
        raise ValueError("confirm_name must exactly match the workspace name")
    with connector.lock:
        row = connector.workspace(name, namespace)
        connector.check_identity(row, expected_workspace_id)
        connector.run(["--delete", name])
        connector.disconnect(name, namespace)
        return {"deletion_requested": True, "name": name, "namespace": namespace}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Read-only connector version and workspace list")
    args = parser.parse_args()
    if args.check:
        print(json.dumps({"runtime": get_runtime_configuration(), "workspaces": list_devspaces()}, indent=2))
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
