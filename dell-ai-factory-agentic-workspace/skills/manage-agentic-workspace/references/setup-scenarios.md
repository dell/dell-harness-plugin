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
| Empty CLI inventory | If still unverified during setup, begin native login automatically; complete one attempt before account/assignment clarification | Empty is never proof of no assignment or trusted certificates |
| Certificate failure in list/auth/connect | Fail closed; ask for the organization CA via `NODE_EXTRA_CA_CERTS`; never retry unverified or replay mutations | Runtime still reports `tls_verification_enabled=true` |
| Login window already active | Reuse its owned process; never create a duplicate | Progress tool reports the same PID |
| Private SSO/MFA/URI prompt | Let the user enter secrets locally; monitor safe progress yourself | Status plus a fresh CLI access check |
| Helper exits or is cancelled | Read failed/cancelled state and recheck CLI before proposing recovery | No user terminal readback required |
| Login says success but CLI remains empty | Recheck origin, CA trust, and namespace filters before asking account/assignment questions | Native banner/exit zero is insufficient |
| URI prompt after SSO | User may press Enter once to finish the attempt; do not fabricate a URI | Follow-up CLI evidence, not the blank entry |
| Workspace already Running | Skip start, establish the requested tunnel | Separate Running and tunnel-ready states |
| Workspace stopped | Ask for the exact startup authorization unless already given | Observed Running after approved start |
| CLI manages SSH configuration | Leave `devspaces.conf`, Includes, host-key pins, and known hosts untouched, including after a failed connect | SSH login checked separately; report whether server identity was verified |
| CLI prints readiness without a newline | The connector backend recognizes the bounded marker stream without waiting for a newline | Ready after the CLI readiness marker is observed and the owned process stays alive; SSH login is separate |
| Tunnel ready, Codex registration pending | Dispatch observed alias and inspect supported app state | Registration/connection are separate from URI dispatch |

## Retained client when chat tools are unavailable

Use the configured cluster/connector environment and run:

```powershell
python <plugin>/scripts/prepare_runtime.py --client --session-dir <new-private-work-directory>
```

The agent substitutes actual absolute paths and executes it. The session directory must be new, preventing reuse of stale responses. Wait for `status.json` with `state=ready`; `failed` indicates initialization/transport failure, and `busy` names the active tool without its arguments. Atomically write `request-1.json` with `{"tool":"get_runtime_configuration","arguments":{}}`, then read `response-1.json`. IDs contain hexadecimal characters/hyphens only; use a new ID for each request. Duplicate IDs are rejected without replaying a tool. The response contains `is_error` and the plugin's sanitized structured `result`; failures add an allowlisted `error` category instead of raw text. The file fallback is read-only: authentication launch, lifecycle operations, connections, and deletions are rejected even when server write gates are enabled. Use the desktop MCP flow with its normal confirmation for changes; do not replace this fallback with a generated client to bypass confirmation. Never place credentials or raw auth output in request/response files.

The client keeps running until an authorized `{"tool":"close"}` request or process shutdown. It cannot create or own a tunnel; connections belong to the desktop MCP session. Use explicit local process ownership for cleanup. Do not overwrite the installed plugin manifest with per-user paths or weaken package-download TLS.

## Terminal visibility

Codex can inspect supported terminals/UI, but full authentication output may contain secrets. Prefer the helper's safe status channel. It reports progress and verified access without terminal scraping or a human success confirmation. Window visibility and authentication are separate: if a launch fails to produce a visible console, inspect the owned process and progress, check whether auth already completed, and recover through a controllable native terminal only if necessary. A user action is justified only for private input or a concrete UI/tool/policy limitation.
