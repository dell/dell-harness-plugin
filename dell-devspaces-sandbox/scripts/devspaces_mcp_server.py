#!/usr/bin/env python3
"""Governed MCP adapter for Eclipse Che and Red Hat OpenShift Dev Spaces."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from typing import Any

from kubernetes import client, config
from kubernetes.client import ApiClient
from kubernetes.client.exceptions import ApiException
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations


DEVWORKSPACE_GROUP = "workspace.devfile.io"
DEVWORKSPACE_VERSION = "v1alpha2"
DEVWORKSPACE_PLURAL = "devworkspaces"
ADMIN_GROUPS = {"system:masters", "cluster-admins"}
WORKSPACE_POD_LABELS = (
    "controller.devfile.io/devworkspace_name",
    "controller.devfile.io/devworkspace_id",
    "devworkspace_id",
)


def _csv(name: str) -> tuple[str, ...]:
    return tuple(value.strip() for value in os.getenv(name, "").split(",") if value.strip())


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    kubeconfig: str | None
    requested_context: str | None
    allowed_contexts: tuple[str, ...]
    allowed_namespaces: tuple[str, ...]
    enable_writes: bool
    enable_delete: bool

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            kubeconfig=os.getenv("DEVSPACES_KUBECONFIG") or os.getenv("KUBECONFIG"),
            requested_context=os.getenv("DEVSPACES_CONTEXT"),
            allowed_contexts=_csv("DEVSPACES_ALLOWED_CONTEXTS"),
            allowed_namespaces=_csv("DEVSPACES_ALLOWED_NAMESPACES"),
            enable_writes=_bool("DEVSPACES_ENABLE_WRITES"),
            enable_delete=_bool("DEVSPACES_ENABLE_DELETE"),
        )


class DevSpacesClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        contexts, active = config.list_kube_config_contexts(config_file=settings.kubeconfig)
        if not contexts or not active:
            raise RuntimeError("No Kubernetes context is available")

        context_names = {item["name"] for item in contexts}
        selected = settings.requested_context or active["name"]
        if selected not in context_names:
            raise RuntimeError(f"Requested context {selected!r} does not exist")

        allowed_contexts = set(settings.allowed_contexts) or {active["name"]}
        if selected not in allowed_contexts:
            raise PermissionError(
                f"Context {selected!r} is outside DEVSPACES_ALLOWED_CONTEXTS"
            )

        config.load_kube_config(config_file=settings.kubeconfig, context=selected)
        self.context = selected
        self.api_client = ApiClient()
        self.custom = client.CustomObjectsApi(self.api_client)
        self.core = client.CoreV1Api(self.api_client)
        self.auth = client.AuthenticationV1Api(self.api_client)

        review = self.auth.create_self_subject_review(
            client.V1SelfSubjectReview(
                api_version="authentication.k8s.io/v1",
                kind="SelfSubjectReview",
            )
        )
        status = review.status
        self.username = status.user_info.username if status and status.user_info else "unknown"
        self.groups = tuple(status.user_info.groups or ()) if status and status.user_info else ()
        self.namespaces = self._resolve_namespaces()

    def _resolve_namespaces(self) -> tuple[str, ...]:
        configured = tuple(dict.fromkeys(self.settings.allowed_namespaces))
        if "*" in configured:
            raise PermissionError("Wildcard namespace authorization is not allowed")
        if configured:
            return configured
        if self.username == "system:admin" or ADMIN_GROUPS.intersection(self.groups):
            raise PermissionError(
                "Administrative identities require DEVSPACES_ALLOWED_NAMESPACES"
            )

        discovered: list[str] = []
        for namespace in self.core.list_namespace().items:
            annotations = namespace.metadata.annotations or {}
            if annotations.get("che.eclipse.org/username") == self.username:
                discovered.append(namespace.metadata.name)
        if not discovered:
            raise PermissionError(
                "No owned Dev Spaces namespace was discovered; configure an explicit allowlist"
            )
        return tuple(sorted(discovered))

    def _authorize_namespace(self, namespace: str) -> None:
        if namespace not in self.namespaces:
            raise PermissionError(f"Namespace {namespace!r} is not authorized")

    def runtime_configuration(self) -> dict[str, Any]:
        host = self.api_client.configuration.host
        return {
            "context": self.context,
            "api_server": host,
            "username": self.username,
            "groups": list(self.groups),
            "authorized_namespaces": list(self.namespaces),
            "writes_enabled": self.settings.enable_writes,
            "delete_enabled": self.settings.enable_delete,
            "interface": "Kubernetes API; oc is not required",
        }

    @staticmethod
    def summarize_workspace(workspace: dict[str, Any]) -> dict[str, Any]:
        metadata = workspace.get("metadata", {})
        spec = workspace.get("spec", {})
        status = workspace.get("status", {})
        return {
            "name": metadata.get("name"),
            "namespace": metadata.get("namespace"),
            "started": bool(spec.get("started", False)),
            "phase": status.get("phase", "Unknown"),
            "message": status.get("message"),
            "main_url": status.get("mainUrl"),
            "created": metadata.get("creationTimestamp"),
        }

    def list_workspaces(self, namespace: str | None = None) -> list[dict[str, Any]]:
        namespaces = (namespace,) if namespace else self.namespaces
        results: list[dict[str, Any]] = []
        for item_namespace in namespaces:
            self._authorize_namespace(item_namespace)
            response = self.custom.list_namespaced_custom_object(
                DEVWORKSPACE_GROUP,
                DEVWORKSPACE_VERSION,
                item_namespace,
                DEVWORKSPACE_PLURAL,
            )
            results.extend(self.summarize_workspace(item) for item in response.get("items", []))
        return sorted(results, key=lambda item: (item["namespace"] or "", item["name"] or ""))

    def get_workspace(self, namespace: str, name: str) -> dict[str, Any]:
        self._authorize_namespace(namespace)
        workspace = self.custom.get_namespaced_custom_object(
            DEVWORKSPACE_GROUP,
            DEVWORKSPACE_VERSION,
            namespace,
            DEVWORKSPACE_PLURAL,
            name,
        )
        return self.summarize_workspace(workspace)

    def get_events(self, namespace: str, name: str, limit: int = 30) -> list[dict[str, Any]]:
        self._authorize_namespace(namespace)
        self.get_workspace(namespace, name)
        events = self.core.list_namespaced_event(
            namespace,
            field_selector=f"involvedObject.kind=DevWorkspace,involvedObject.name={name}",
        ).items
        events.sort(
            key=lambda event: str(
                event.last_timestamp or event.event_time or event.first_timestamp or ""
            )
        )
        return [
            {
                "type": event.type,
                "reason": event.reason,
                "message": event.message,
                "count": event.count,
                "last_seen": str(
                    event.last_timestamp or event.event_time or event.first_timestamp or ""
                ),
            }
            for event in events[-max(1, min(limit, 100)) :]
        ]

    def _workspace_pods(self, namespace: str, name: str) -> list[Any]:
        pods: dict[str, Any] = {}
        for label in WORKSPACE_POD_LABELS:
            for pod in self.core.list_namespaced_pod(
                namespace, label_selector=f"{label}={name}"
            ).items:
                pods[pod.metadata.name] = pod
        return list(pods.values())

    def get_logs(
        self,
        namespace: str,
        name: str,
        tail_lines: int = 200,
        container: str | None = None,
    ) -> list[dict[str, Any]]:
        self._authorize_namespace(namespace)
        self.get_workspace(namespace, name)
        bounded_tail = max(1, min(tail_lines, 500))
        output: list[dict[str, Any]] = []
        for pod in self._workspace_pods(namespace, name):
            containers = [item.name for item in pod.spec.containers]
            selected = [container] if container else containers
            for container_name in selected:
                if container_name not in containers:
                    continue
                try:
                    logs = self.core.read_namespaced_pod_log(
                        pod.metadata.name,
                        namespace,
                        container=container_name,
                        tail_lines=bounded_tail,
                        timestamps=True,
                    )
                except ApiException as exc:
                    logs = f"Log retrieval failed: Kubernetes API status {exc.status}"
                output.append(
                    {
                        "pod": pod.metadata.name,
                        "container": container_name,
                        "tail_lines": bounded_tail,
                        "logs": logs,
                    }
                )
        return output

    def set_started(self, namespace: str, name: str, started: bool) -> dict[str, Any]:
        if not self.settings.enable_writes:
            raise PermissionError("Lifecycle writes are disabled")
        self._authorize_namespace(namespace)
        self.custom.patch_namespaced_custom_object(
            DEVWORKSPACE_GROUP,
            DEVWORKSPACE_VERSION,
            namespace,
            DEVWORKSPACE_PLURAL,
            name,
            {"spec": {"started": started}},
        )
        return self.get_workspace(namespace, name)

    def delete_workspace(
        self, namespace: str, name: str, confirm_name: str
    ) -> dict[str, Any]:
        if not self.settings.enable_delete:
            raise PermissionError("Workspace deletion is disabled")
        if confirm_name != name:
            raise ValueError("confirm_name must exactly match the workspace name")
        self._authorize_namespace(namespace)
        self.custom.delete_namespaced_custom_object(
            DEVWORKSPACE_GROUP,
            DEVWORKSPACE_VERSION,
            namespace,
            DEVWORKSPACE_PLURAL,
            name,
            body=client.V1DeleteOptions(propagation_policy="Foreground"),
        )
        return {"deleted": name, "namespace": namespace}


SETTINGS = Settings.from_env()
_client: DevSpacesClient | None = None


def get_client() -> DevSpacesClient:
    global _client
    if _client is None:
        _client = DevSpacesClient(SETTINGS)
    return _client


READ = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
)
WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True
)
DELETE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True
)
mcp = FastMCP("Dell Dev Spaces Sandbox")


@mcp.tool(annotations=READ)
def list_clusters() -> dict[str, Any]:
    """Return the single selected and authorized cluster context."""
    runtime = get_client().runtime_configuration()
    return {"clusters": [{"context": runtime["context"], "api_server": runtime["api_server"]}]}


@mcp.tool(annotations=READ)
def get_runtime_configuration() -> dict[str, Any]:
    """Return identity, context, namespace boundary, and write gates."""
    return get_client().runtime_configuration()


@mcp.tool(annotations=READ)
def list_devspaces(namespace: str | None = None) -> list[dict[str, Any]]:
    """List minimal status for workspaces in authorized namespaces."""
    return get_client().list_workspaces(namespace)


@mcp.tool(annotations=READ)
def get_devspace_status(namespace: str, name: str) -> dict[str, Any]:
    """Get minimal status for one authorized DevWorkspace."""
    return get_client().get_workspace(namespace, name)


@mcp.tool(annotations=READ)
def get_devspace_events(
    namespace: str, name: str, limit: int = 30
) -> list[dict[str, Any]]:
    """Get recent Kubernetes events scoped to one DevWorkspace."""
    return get_client().get_events(namespace, name, limit)


@mcp.tool(annotations=READ)
def get_devspace_logs(
    namespace: str,
    name: str,
    tail_lines: int = 200,
    container: str | None = None,
) -> list[dict[str, Any]]:
    """Get bounded logs from pods labeled for one DevWorkspace; no pod exec."""
    return get_client().get_logs(namespace, name, tail_lines, container)


@mcp.tool(annotations=WRITE)
def start_devspace(namespace: str, name: str) -> dict[str, Any]:
    """Set spec.started=true when lifecycle writes are explicitly enabled."""
    return get_client().set_started(namespace, name, True)


@mcp.tool(annotations=WRITE)
def stop_devspace(namespace: str, name: str) -> dict[str, Any]:
    """Set spec.started=false when lifecycle writes are explicitly enabled."""
    return get_client().set_started(namespace, name, False)


@mcp.tool(annotations=DELETE)
def delete_devspace(namespace: str, name: str, confirm_name: str) -> dict[str, Any]:
    """Delete one workspace only when deletion is enabled and confirmation matches."""
    return get_client().delete_workspace(namespace, name, confirm_name)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Validate configuration and perform a read-only list"
    )
    args = parser.parse_args()
    if args.check:
        adapter = get_client()
        print(
            json.dumps(
                {
                    "runtime": adapter.runtime_configuration(),
                    "workspaces": adapter.list_workspaces(),
                },
                indent=2,
                default=str,
            )
        )
        return
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
