# Setup scenarios and recovery

Configure/connect requests include initiating normal native login as needed. Run commands yourself; the user handles only private SSO/MFA and credential prompts. Do not require a second request to initiate login, create a per-chat Python client, or ask the user to report terminal success.

| Observed condition | Agent action | Completion evidence |
| --- | --- | --- |
| Python has MCP stdio dependencies | Reuse it through prepare_runtime.py | Exact MCP version and stdio import check |
| Python exists, MCP missing or broken | Bootstrap a reusable isolated venv; install pinned MCP using normal verified package transport | Import check passes before server starts |
| Python command absent or Windows Store alias | Resolve an installed interpreter with local command/runtime discovery and configure its absolute executable; do not tell the user to run Python commands | Interpreter starts and bootstrap passes |
| Runtime install blocked by sandbox/network | Use the normal tool escalation path; report a real policy/package-network blocker if rejected | No false missing-package diagnosis from access denial |
| MCP tools absent in this chat | Run bundled bootstrap/client with a new private session directory | status.json is ready and tool results arrive |
| Existing authenticated inventory | Skip login; continue selection and connect | Correct cluster and observed workspace IDs |
| Empty CLI inventory | One differential TLS read when configured; if still unverified during setup, begin native login automatically | Empty is never proof of no assignment or trusted certificates |
| Certificate failure in list/auth | One configured child-only TLS retry; never replay lifecycle mutations | Runtime reports effective TLS mode |
| Empty under verified TLS, populated under exception | Keep the successful session mode for list/connect | Correct cluster and IDs, TLS false reported |
| Both TLS modes empty | Restore prior mode; complete one native login attempt before account/assignment clarification | Unverified state remains explicit |
| Login window already active | Reuse its owned process; never create a duplicate | Progress tool reports the same PID |
| Private SSO/MFA/URI prompt | Let the user enter secrets locally; monitor safe progress yourself | Status plus a fresh CLI access check |
| Helper exits or is cancelled | Read failed/cancelled state and recheck CLI before proposing recovery | No user terminal readback required |
| Login says success but CLI remains empty | Finish differential TLS check before asking account/assignment questions | Native banner/exit zero is insufficient |
| URI prompt after SSO | User may press Enter once to finish the attempt; do not fabricate a URI | Follow-up CLI evidence, not the blank entry |
| Workspace already Running | Skip start, establish the requested tunnel | Separate Running and tunnel-ready states |
| Workspace stopped | Ask for the exact startup authorization unless already given | Observed Running after approved start |
| Connector rewrites SSH config insecurely | Preserve/restore existing verified pinned-key settings; keep strict checking | Real strict SSH probe; never accept an unknown key automatically |
| Tunnel ready, Codex registration pending | Dispatch observed alias and inspect supported app state | Registration/connection are separate from URI dispatch |

## Retained client when chat tools are unavailable

Use the configured cluster/connector environment and run:

```powershell
python <plugin>/scripts/prepare_runtime.py --client --session-dir <new-private-work-directory>
```

The agent substitutes actual absolute paths and executes it. The session directory must be new, preventing reuse of stale responses. Wait for `status.json` with `state=ready`. Atomically write `request-1.json` with `{"tool":"get_runtime_configuration","arguments":{}}`, then read `response-1.json`. IDs contain hexadecimal characters/hyphens only; use a new ID for each request. The response contains `is_error` and the plugin's sanitized structured `result`. Call `begin_devspaces_authentication`, then `get_devspaces_authentication_progress` through this same retained client; the effective TLS mode stays with its connector and tunnels. Never place credentials or raw auth output in request/response files.

The client keeps running until an authorized `{"tool":"close"}` request or process shutdown. Closing it closes its owned tunnels; do not use a one-shot process for a connection that must remain alive. Use explicit local process ownership for cleanup. Do not overwrite the installed plugin manifest with per-user paths or weaken package-download TLS.

## Terminal visibility

Codex can inspect supported terminals/UI, but full authentication output may contain secrets. Prefer the helper's safe status channel. It reports progress and verified access without terminal scraping or a human success confirmation. Window visibility and authentication are separate: if a launch fails to produce a visible console, inspect the owned process and progress, check whether auth already completed, and recover through a controllable native terminal only if necessary. A user action is justified only for private input or a concrete UI/tool/policy limitation.
