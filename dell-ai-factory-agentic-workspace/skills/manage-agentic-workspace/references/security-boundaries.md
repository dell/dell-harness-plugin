# Connector security boundaries

The connector runs as the local user with its authenticated account. Platform authorization remains authoritative. DEVSPACES_ALLOWED_NAMESPACES optionally narrows access; this backend cannot detect administrative group membership. Use a least-privilege account. Every targeted remote action lists first and verifies cluster, namespace, and name uniqueness across the account.

Start, stop, and restart require DEVSPACES_ENABLE_WRITES=true. Delete also requires DEVSPACES_ENABLE_DELETE=true and a matching name. An explicit user request supplies action authorization; configured write gates are a separate deployment control. Explain disabled gates rather than changing them silently.

Connecting does not require DEVSPACES_ENABLE_WRITES: it changes no remote workspace state, the CLI owns SSH configuration, and the plugin itself only removes complete legacy plugin-owned guard blocks for the selected alias. It starts a long-lived local port forward and is annotated destructive so clients still confirm it. Disconnect only terminates owned processes. No generic shell, credential-read, RBAC, or Kubernetes mutation tool is exposed. Raw subprocess errors and connection output are discarded because they may contain authentication data.

Authenticate in a native local terminal using the machine's default browser, never the ChatGPT in-app browser. The agent runs commands and helpers; only SSO/credentials/private prompts require user input. Never read credential stores, private keys, or auth URIs into chat. Leave SSH configuration and host-key policy to the CLI and existing user/organization settings. The plugin removes only complete legacy plugin-owned guards for the selected alias; it never edits known-host keys or adds Includes. CLI settings can disable server identity verification; a successful login under those settings does not verify identity.

The server ignores ambient NODE_TLS_REJECT_UNAUTHORIZED. Prefer NODE_EXTRA_CA_CERTS with an approved CA. DEVSPACES_AUTO_TLS_RETRY defaults to true: a diagnosed certificate failure during read-only --list triggers one child-process retry with verification disabled. The exception then applies to every later connector call in that cluster's session, including lifecycle actions, delete, and tunnels, and to its children; it is warned about once, and is exposed in runtime state. It can permit interception. Set DEVSPACES_AUTO_TLS_RETRY=false when verified TLS is required; explicit user or cluster requirements take precedence. DEVSPACES_ALLOW_INSECURE_TLS=true carries an existing session exception. Never persist TLS exceptions, apply them to unrelated errors/clusters, change SSH policy as a TLS workaround, or automatically replay mutations. The native auth helper uses the same read-only preflight, keeps private input/output on the native terminal, forwards stderr there without logging while classifying certificate errors, and verifies CLI access afterward. A certificate failure during authentication can trigger one auth retry, including when the preceding list silently returned empty. Never replay a lifecycle/delete action or capture the native authentication terminal through a tool. Do not redistribute connector executables or credentials.

Avoid external auth commands during operations: preflight checks cannot make the connector's external credential state transactional. This MCP process serializes its mutations and stops if workspace identity changes during a transition.


During explicitly requested setup, native login initiation is already within
scope. The new begin/progress tools expose only whitelisted safe status, not
terminal output. A private per-job status file binds progress to the selected
cluster and carries its effective TLS setting. With automatic TLS retry enabled,
an inconclusive empty inventory permits one differential read; retain an
exception only if it verifies the intended cluster. This is a diagnostic, not
proof of a certificate failure. Both empty modes remain inconclusive.

The runtime bootstrap installs pinned MCP into a reusable isolated venv and
uses verified package transport; connector TLS exceptions never weaken pip or
SSH trust. The retained client uses a new agent-controlled private directory
for requests/responses and never stores credential prompts or raw exceptions.
