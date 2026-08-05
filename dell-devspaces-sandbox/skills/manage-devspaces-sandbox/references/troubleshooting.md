# Troubleshooting

## Server will not start

Confirm Python 3 and the pinned `mcp` and `kubernetes` packages are available. Confirm kubeconfig is readable and contains the selected context.

## SSO browser does not open

Confirm the managed runtime provides `oc` on `PATH` or sets `DEVSPACES_OC_COMMAND`. Confirm the workstation can reach the registered API URL on port 6443. The plugin will not fall back to asking for a token.

## Instance is missing or an endpoint changed

Update the enterprise JSON file selected by `DEVSPACES_INSTANCES_FILE`. Keep one stable logical name per instance and change its API/UI URLs centrally. Do not accept an unregistered URL from a prompt.

## No authorized namespaces

For administrative identities, set a comma-separated `DEVSPACES_ALLOWED_NAMESPACES` allowlist. For regular users, verify that the Dev Spaces user namespace has ownership metadata and that RBAC permits listing that namespace.

## Wrong cluster

Set `DEVSPACES_CONTEXT` to the intended context and include it in `DEVSPACES_ALLOWED_CONTEXTS`. Never silently switch clusters to satisfy a failed request.

## Write rejected

Start and stop require `DEVSPACES_ENABLE_WRITES=true`. Deletion also requires `DEVSPACES_ENABLE_DELETE=true` and an exact confirmation string matching the workspace name.

## Failed workspace

1. Read workspace status and message.
2. Read warning and normal events scoped to the DevWorkspace.
3. Request bounded logs only from pods labeled for that DevWorkspace.
4. Report the likely failing component and owner; do not broaden access or execute a shell in the workspace.
