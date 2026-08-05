# Security boundaries

## Trust model

The plugin runs with the Kubernetes or OpenShift identity already present in the selected kubeconfig. It does not create credentials, elevate RBAC, or impersonate users. Cluster RBAC remains the primary authorization boundary; the plugin adds narrower context, namespace, tool, and write gates.

For SSO, the plugin invokes the managed `oc login <registered-api-url> --web` workflow. The user completes the corporate browser challenge. The plugin captures no password, never returns command output, and does not expose the resulting OAuth token to Codex. OpenShift stores the session in the selected kubeconfig.

## Tool boundary

Allowed operations are limited to DevWorkspace discovery, status, events, bounded workspace logs, desired-state start or stop, and separately gated deletion. The MCP server intentionally provides no generic Kubernetes CRUD, pod exec, shell, namespace deletion, Secret reads, or RBAC mutation.

Administrative identities must configure an explicit `DEVSPACES_ALLOWED_NAMESPACES` value. Wildcards are rejected. Non-administrative identities may use explicitly configured namespaces or namespaces discovered from Dev Spaces ownership metadata.

## Production requirements

- Use OpenShift OAuth or another short-lived, per-user credential flow.
- Provide `oc` in the managed runtime and maintain the approved instance catalog centrally.
- Pin the target context with `DEVSPACES_ALLOWED_CONTEXTS`.
- Enforce namespace-scoped RBAC independently of plugin configuration.
- Keep TLS verification and SSH host-key verification enabled.
- Ship the Python dependencies in a signed, versioned runtime image or package.
- Record lifecycle mutations in platform audit logs and forward relevant Codex telemetry to the approved observability stack.
- Keep write operations disabled until the pilot security and support reviews are complete.

The plugin never downloads `oc`. Core workspace operations use the Kubernetes API client; the managed `oc` binary is used only to check and initiate SSO.
