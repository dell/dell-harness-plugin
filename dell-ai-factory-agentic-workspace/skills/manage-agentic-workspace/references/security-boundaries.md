# Connector security boundaries

The connector runs as the local user with its authenticated account. Platform authorization remains authoritative. DEVSPACES_ALLOWED_NAMESPACES optionally narrows access; this backend cannot detect administrative group membership. Use a least-privilege account. Every targeted remote action lists first and verifies cluster, namespace, and name uniqueness across the account.

Start, stop, and restart require DEVSPACES_ENABLE_WRITES=true. Delete also requires DEVSPACES_ENABLE_DELETE=true and a matching name. An explicit user request supplies action authorization; configured write gates are a separate deployment control. Explain disabled gates rather than changing them silently.

Connecting does not require DEVSPACES_ENABLE_WRITES: it changes no remote workspace state, the CLI owns SSH configuration, and the plugin never reads or edits SSH configuration or known-hosts files. It starts a long-lived local port forward and is annotated destructive so clients still confirm it. Disconnect only terminates owned processes. No generic shell, credential-read, RBAC, or Kubernetes mutation tool is exposed. Raw subprocess errors and connection output are discarded because they may contain authentication data.

Authenticate in a native local terminal using the machine's default browser, never the ChatGPT in-app browser. The agent runs commands and helpers; only SSO/credentials/private prompts require user input. Never read credential stores, private keys, or auth URIs into chat. Leave SSH configuration and host-key policy to the CLI and existing user/organization settings. The plugin never removes host-key pins (including legacy plugin-written guard blocks), never edits known-host keys, and never adds Includes, so a failed connect or reconnect leaves trust settings unchanged. CLI settings can disable server identity verification; a successful login under those settings does not verify identity.

The server ignores ambient NODE_TLS_REJECT_UNAUTHORIZED. Use NODE_EXTRA_CA_CERTS with an approved CA. Certificate verification is never disabled automatically: a certificate failure in list, sign-in, lifecycle, delete, or connect fails closed with a sanitized error, and no failure, retry, or sign-in result can change the TLS mode of later calls. The only unverified mode is an administrator's DEVSPACES_ALLOW_INSECURE_TLS=true, read once at server start and exposed in runtime state; DEVSPACES_AUTO_TLS_RETRY is ignored. Never change SSH policy as a TLS workaround or automatically replay mutations. The native auth helper uses the same read-only preflight, keeps private input/output on the native terminal, forwards stderr there without logging while classifying certificate errors, and verifies CLI access afterward. A certificate failure during authentication ends that attempt. Never replay a lifecycle/delete action or capture the native authentication terminal through a tool. Do not redistribute connector executables or credentials.

Avoid external auth commands during operations: preflight checks cannot make the connector's external credential state transactional. This MCP process serializes its mutations and stops if workspace identity changes during a transition.


During explicitly requested setup, native login initiation is already within
scope. The new begin/progress tools expose only whitelisted safe status, not
terminal output. A private per-job status file binds progress to the selected
cluster. Its TLS field is informational and never changes the server's TLS mode.
An empty inventory stays inconclusive.

The runtime bootstrap installs pinned MCP into a reusable isolated venv and
uses verified package transport; connector TLS exceptions never weaken pip or
SSH trust. The retained client uses a new agent-controlled private directory
for requests/responses and never stores credential prompts or raw exceptions.
