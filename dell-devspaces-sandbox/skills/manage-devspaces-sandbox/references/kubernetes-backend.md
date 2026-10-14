# Optional legacy Kubernetes backend

This is the pre-0.2.0 backend. To select it explicitly, change the MCP manifest script argument to `${PLUGIN_ROOT}/scripts/devspaces_mcp_server.py` and restart the server. The connector backend is the default. This backend does not provide SSH connect or restart tools.

# Dell Dev Spaces Sandbox plugin

This pilot Codex plugin provides a narrow MCP interface for Eclipse Che and Red Hat OpenShift Dev Spaces. Workspace operations use the Kubernetes API and the DevWorkspace custom resource. A managed `oc` binary is used only to check and initiate corporate SSO with `oc login --web`.

## Security defaults

- The initially active kubeconfig context is the only allowed context unless `DEVSPACES_ALLOWED_CONTEXTS` is configured.
- Administrative identities must set an explicit `DEVSPACES_ALLOWED_NAMESPACES` value; wildcards are rejected.
- Start and stop are disabled unless `DEVSPACES_ENABLE_WRITES=true`.
- Delete is independently disabled unless `DEVSPACES_ENABLE_DELETE=true`, and the tool requires an exact workspace-name confirmation.
- The server has no generic resource mutation, pod exec, Secret, RBAC, or namespace-deletion tools.
- Authentication is limited to API URLs in the managed instance registry. The plugin never asks for or returns an OAuth token.

## Automatic SSO workflow

The skill lists managed instances, selects the named or default instance, and checks the existing OpenShift session. When the session is missing or expired, it calls `authenticate_devspaces`, which launches `oc login <registered-api-url> --web` in the background and returns immediately. The skill polls authentication status while the user completes the corporate browser challenge.

The plugin captures no password or token and discards command output. The managed Codex runtime must provide `oc` on `PATH`; the plugin does not download executables.

## Multiple instances

The bundled [instance catalog](../../../config/instances.json) maps stable logical names to credential-free API and UI URLs. Add instances or update changing endpoints centrally:

```json
{
  "instances": [
    {
      "name": "example-devspaces",
      "display_name": "Example Dev Spaces cluster",
      "api_url": "https://api.devspaces.example.com:6443",
      "ui_url": "https://devspaces.example.com",
      "default": true
    }
  ]
}
```

For managed rollout, set `DEVSPACES_INSTANCES_FILE` to an enterprise-maintained copy. Set `DEVSPACES_DEFAULT_INSTANCE` when the catalog contains multiple entries without a default. Prompt-supplied URLs are never used.

## Runtime configuration

Export configuration before starting Codex:

```bash
export DEVSPACES_CONTEXT=my-openshift-context
export DEVSPACES_ALLOWED_CONTEXTS=my-openshift-context
export DEVSPACES_ALLOWED_NAMESPACES=my-user-devspaces
export DEVSPACES_DEFAULT_INSTANCE=example-devspaces
```

Keep the write variables unset for read-only evaluation. Use an OpenShift OAuth-backed, non-admin kubeconfig for the production pilot.

## Install from this repository

```bash
codex plugin marketplace add https://github.com/dell/dell-harness-plugin.git
codex plugin add dell-devspaces-sandbox@dell-harness-plugin
```

The server requires the packages constrained in `requirements.txt`. The supported distribution should include these dependencies in the Codex runtime image.

For a pilot workstation installation:

```powershell
py -m pip install -r .\dell-devspaces-sandbox\requirements.txt
```

Confirm that `python` is available on `PATH` before restarting Codex. For
managed rollout, bake these dependencies into the supported Codex runtime
rather than installing them interactively.

## Validation

```bash
python3 -m pytest dell-devspaces-sandbox/tests -q
DEVSPACES_ALLOWED_NAMESPACES=my-user-devspaces \
  python3 dell-devspaces-sandbox/scripts/devspaces_mcp_server.py --check
```

The `--check` command validates identity and authorization, then performs only a DevWorkspace list.
