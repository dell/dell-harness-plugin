---
name: manage-devspaces-sandbox
description: Safely inspect, diagnose, start, stop, or delete an authorized Eclipse Che or Red Hat OpenShift Dev Spaces workspace through the dell_devspaces MCP server. Use for workspace lifecycle requests, failed-workspace troubleshooting, and verification of the active cluster and namespace boundary.
---

# Manage Dev Spaces Sandbox

Use the `dell_devspaces` MCP server as the governed interface. Keep operations inside the configured context and namespace allowlists.

## Workflow

1. Call `get_runtime_configuration` before acting. State the active cluster context, authenticated identity, write mode, and authorized namespaces.
2. Confirm that the requested workspace is in an authorized namespace. Use `list_devspaces` when the exact name is unknown.
3. Use `get_devspace_status`; for failures, inspect `get_devspace_events` before requesting bounded workspace logs.
4. For start or stop requests, name the exact workspace and explain that the operation changes its desired state. Then call the matching tool.
5. Delete only when the user explicitly requests deletion and the exact confirmation value matches the workspace name.

## Guardrails

- Fail closed when cluster context or namespace authorization is ambiguous.
- Do not substitute generic Kubernetes CRUD, pod exec, namespace deletion, Secret access, or RBAC changes.
- Never retrieve or expose SSH private keys, tokens, or full Secret objects.
- Never bypass TLS verification or SSH host-key checking.
- Return only the minimum diagnostics needed; avoid dumping complete pod logs or resource specifications.
- Treat writes and deletion as disabled unless runtime configuration explicitly enables them.

Read [security-boundaries.md](references/security-boundaries.md) before changing lifecycle state. For failed workspaces, follow [troubleshooting.md](references/troubleshooting.md).

## Runtime configuration

- `DEVSPACES_ALLOWED_CONTEXTS`: comma-separated kubeconfig contexts; unset means only the initially active context.
- `DEVSPACES_ALLOWED_NAMESPACES`: comma-separated namespace allowlist. It is mandatory for administrative identities.
- `DEVSPACES_ENABLE_WRITES=false`: enables start and stop only when set to `true`.
- `DEVSPACES_ENABLE_DELETE=false`: separately enables deletion only when set to `true`.

## Expected outcome

Report the workspace name, namespace, phase, and result. If blocked, identify the precise authorization, configuration, or platform dependency without broadening access.
