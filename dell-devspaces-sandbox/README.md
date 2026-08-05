# Dell Dev Spaces Sandbox plugin

This pilot Codex plugin provides a narrow MCP interface for Eclipse Che and Red Hat OpenShift Dev Spaces. It uses the Kubernetes API and the DevWorkspace custom resource, so it does not install or require `oc`.

## Security defaults

- The initially active kubeconfig context is the only allowed context unless `DEVSPACES_ALLOWED_CONTEXTS` is configured.
- Administrative identities must set an explicit `DEVSPACES_ALLOWED_NAMESPACES` value; wildcards are rejected.
- Start and stop are disabled unless `DEVSPACES_ENABLE_WRITES=true`.
- Delete is independently disabled unless `DEVSPACES_ENABLE_DELETE=true`, and the tool requires an exact workspace-name confirmation.
- The server has no generic resource mutation, pod exec, Secret, RBAC, or namespace-deletion tools.

## Runtime configuration

Export configuration before starting Codex:

```bash
export DEVSPACES_CONTEXT=my-openshift-context
export DEVSPACES_ALLOWED_CONTEXTS=my-openshift-context
export DEVSPACES_ALLOWED_NAMESPACES=my-user-devspaces
```

Keep the write variables unset for read-only evaluation. Use an OpenShift OAuth-backed, non-admin kubeconfig for the production pilot.

## Install from this repository

```bash
codex plugin marketplace add https://github.com/dell/dell-harness-plugin.git
codex plugin add dell-devspaces-sandbox@dell-harness-plugin
```

The server requires the packages pinned in `requirements.txt`. The supported distribution should include these dependencies in the Codex runtime image.

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
