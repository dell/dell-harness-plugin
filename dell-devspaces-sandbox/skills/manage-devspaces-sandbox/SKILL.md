---
name: manage-devspaces-sandbox
description: Select and authenticate to a managed Eclipse Che or Red Hat OpenShift Dev Spaces instance, then safely inspect, diagnose, start, stop, or delete an authorized workspace through the dell_devspaces MCP server. Use for SSO login, instance selection, workspace lifecycle requests, failed-workspace troubleshooting, and verification of cluster and namespace boundaries.
---

# Manage Dev Spaces Sandbox

Use the `dell_devspaces` MCP server as the governed interface. Resolve endpoints from the managed instance registry; never ask the user to type an instance URL or paste an OAuth token.

## Workflow

1. Call `list_devspaces_instances`. Use a user-named instance; otherwise use the configured default. Ask for an instance choice only when multiple entries exist without a default.
2. Call `get_authentication_status` for that instance. If authentication is missing or expired, immediately call `authenticate_devspaces`. Tell the user only that the corporate SSO browser challenge must be completed; never request its token.
3. While `login_state` is `waiting_for_browser`, poll `get_authentication_status` without starting another login. Continue only when `authenticated` is true; report a failed login without exposing command output.
4. After successful login, call `get_runtime_configuration`. State the selected instance, active context, authenticated identity, write mode, and authorized namespaces.
5. Confirm that the requested workspace is in an authorized namespace. Use `list_devspaces` when the exact name is unknown.
6. Use `get_devspace_status`; for failures, inspect `get_devspace_events` before requesting bounded workspace logs.
7. For start or stop requests, name the exact workspace and explain that the operation changes its desired state. Then call the matching tool.
8. Delete only when the user explicitly requests deletion and the exact confirmation value matches the workspace name.

## Guardrails

- Fail closed when cluster context or namespace authorization is ambiguous.
- Do not substitute generic Kubernetes CRUD, pod exec, namespace deletion, Secret access, or RBAC changes.
- Never retrieve or expose SSH private keys, tokens, or full Secret objects.
- Never accept an API URL supplied in a prompt; use only a named entry from the managed registry.
- Never echo `oc login` output or read credentials from kubeconfig.
- Never bypass TLS verification or SSH host-key checking.
- Return only the minimum diagnostics needed; avoid dumping complete pod logs or resource specifications.
- Treat writes and deletion as disabled unless runtime configuration explicitly enables them.

Read [security-boundaries.md](references/security-boundaries.md) before changing lifecycle state. For failed workspaces, follow [troubleshooting.md](references/troubleshooting.md).

## Runtime configuration

- `DEVSPACES_INSTANCES_FILE`: enterprise-managed JSON instance catalog; defaults to the bundled catalog.
- `DEVSPACES_DEFAULT_INSTANCE`: logical instance name to select when the user does not specify one.
- `DEVSPACES_OC_COMMAND`: managed `oc` executable name or path used only for SSO status and `login --web`.
- `DEVSPACES_ALLOWED_CONTEXTS`: comma-separated kubeconfig contexts; unset means only the initially active context.
- `DEVSPACES_ALLOWED_NAMESPACES`: comma-separated namespace allowlist. It is mandatory for administrative identities.
- `DEVSPACES_ENABLE_WRITES=false`: enables start and stop only when set to `true`.
- `DEVSPACES_ENABLE_DELETE=false`: separately enables deletion only when set to `true`.

## Expected outcome

Report the workspace name, namespace, phase, and result. If blocked, identify the precise authorization, configuration, or platform dependency without broadening access.
