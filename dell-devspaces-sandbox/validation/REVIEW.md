# Dev Spaces plugin pre-push review

## 0.4.0 update: runtime and setup orchestration

An independent Windows end-to-end pass exercised a clean runtime, concurrent
runtime preparation, authentication reuse, silent-empty TLS recovery, inventory,
and actual fresh tunnels. It found connector-owned SSH Includes disabling trust
and a readiness marker without a trailing newline. The candidate now preserves
strict pinned trust before Includes and recognizes readiness without a newline;
repair failures or a missing SSH configuration prevent a ready result. Native
authentication/status was separately exercised with a real synthetic executable
in a new console, without terminal readback or real credential input.

Live strict SSH rejected a changed host key on both new and old endpoints. No
unverified key was accepted or replaced. This prevents claiming complete live
SSH/Codex acceptance until independent key validation is available; tunnel-ready
is explicitly separate. Fresh real SSO/MFA was not forced by revoking an existing
session. Retained-client failures now expose safe categories, and duplicate
request IDs cannot automatically replay a tool invocation.

The candidate adds reusable isolated runtime preparation, a retained MCP client,
two native-authentication launch/progress tools (14 tools total), a whitelisted
status channel, and differential recovery for silent-empty CLI inventories.
Prior 0.2/0.3 observations are historical; current generated evidence labels
offline tests separately from live read-only and interactive acceptance.

Cold-start runtime creation and reuse, new-tool output schemas, native helper
status privacy, duplicate-window prevention, session TLS continuity, and
silent-empty recovery are explicit checks. Fresh installed-chat SSO and SSH
remain separate acceptance; no lifecycle mutation or host-key relaxation is
authorized by this update. Runtime preparation uses verified package TLS.

## Historical 0.3.0 update: native authentication

The current candidate adds a standard-library Python native-terminal authentication
helper, PowerShell/batch wrappers, CLI-only status guidance, and bounded automatic
certificate retries. The 0.2.0 observations below remain historical evidence and
do not establish acceptance of the new candidate. Run `scripts/validate_plugin.py`
to generate current synthetic protocol, helper, and relocated-package evidence.
No live lifecycle mutation or new SSH trust decision is part of this update.

The default TLS behavior changed: a diagnosed certificate failure in CLI list or
interactive auth can trigger one child-scoped retry without verification. This
permits interception; `DEVSPACES_AUTO_TLS_RETRY=false` disables automatic fallback.
Runtime reports the effective state. Mutations are never replayed. Acceptance
must distinguish process launch, visible native terminal, machine-browser SSO,
verified CLI cluster access, tunnel readiness, and strict SSH verification.

## Historical 0.2.0 review


Review date: 2026-09-30. Candidate version: 0.2.0. Scope: Git repository distribution as a local Codex plugin.

## Decision

**Development merge; production acceptance remains incomplete.** The maintainers approved merging to main after reviewing the remaining gaps. This is not approval of a production release or plugin-directory submission. Offline validation establishes the implementation and package contract; live results below cover only the observed operations.

### Live acceptance observed on 2026-09-30

- Installed the connector-based v0.2.0 through an isolated local marketplace. MCP initialization and all 12 tool definitions succeeded. The local installation uses explicit bundled Python/dependency paths; the repository's portable manifest still requires its documented runtime prerequisites.
- A real certificate-verification failure was reproduced. A previously authorized, process-scoped TLS retry verified existing authentication and non-empty inventory for the selected test cluster; no new login was needed. No global TLS exception was configured.
- Invoked installed MCP `connect_devspace`, verified an actual new `--connect` process and listening tunnel, then verified strict SSH with a previously pinned host key and matching remote workspace identity.
- Dispatched the Codex registration URL with `enabled=true`. Actual desktop Connected status and remote Codex app-server operation have not been confirmed; URL dispatch alone is not acceptance evidence for them.
- Windows authentication helper and portable launcher tested with eight synthetic cases. Total automated suite: 48 passed. These are not model-driven workflow evaluations.

Outstanding acceptance includes complete live lifecycle/disconnect/reconnect, client timeout/cancellation, fresh-chat evaluations, and clean-machine installation. Existing tunnels were preserved during testing; repeated fresh connections require explicit ownership and cleanup planning. Detailed machine-specific live artifacts stay outside the repository; do not publish user credentials or raw authentication logs.

Sources reviewed: the user-provided August 2026 plugin guide (all 16 pages; relevant checklist pages visually inspected) and current official OpenAI packaging, testing, and plugin guidelines. The source PDF is not bundled or copied into this repository. Document examples were treated as review material, not execution instructions.

Official references:

- https://developers.openai.com/plugins/build/plugins
- https://developers.openai.com/plugins/deploy/connect-chatgpt
- https://developers.openai.com/plugins/plugin-guidelines
- https://developers.openai.com/codex/config-reference

## Findings and changes

| Area | Finding | Resolution / evidence |
| --- | --- | --- |
| Product scope | Core lifecycle and connect flows need a focused, explicit tool surface | 12 named MCP tools; utility-only create/dashboard/URL-handler features excluded from v0.2 promises |
| Packaging | Local compatibility manifest is supported; no registered hosted app exists | Keep .codex-plugin/plugin.json and .mcp.json; do not invent an .app.json or app ID |
| Identity and metadata | Missing repository/homepage and visual assets | Added repository/homepage, Dell author URL, brand color, and existing repository Dell wordmark; stable installation ID retained |
| Inputs | Workspace names, timeouts, and pagination lacked constraints in the published schema | Bounded annotated inputs, exact names, and required expected_workspace_id for remote actions |
| Stable identity | A name could refer to a replacement workspace since the user's earlier read | Compare observed devworkspaceId before mutation; verify identity while waiting; do not reuse a tunnel for a replacement ID |
| Context efficiency | list_devspaces returned all workspaces to the model | Default 25 / maximum 100 per page, exact-name/namespace filters, total and next_offset; pages are live inventory, not a snapshot |
| Output contract | Bare dictionaries did not specify result fields | Typed output schemas; reject malformed connector fields; validate successful responses over actual MCP transport |
| Schema runtime | New optional output fields failed actual transport validation even though direct unit calls passed | Corrected nullable output fields; protocol regression tests cover normal and empty results |
| Annotations | Disconnect was labeled non-destructive although it interrupts a connection | Explicit conservative annotations for connect/disconnect/stop/delete; restart non-idempotent; private account scope marked closed-world |
| Authentication | Auth tool name suggested it performed login | Renamed to get_devspaces_authentication_instructions; returns local interactive instructions; empty lists never prove cluster authentication |
| Credential handling | Raw CLI errors and inherited TLS bypass could expose or weaken authentication | Sanitized errors, no credential reads, verified TLS by default, CA configuration documented; explicit process-scoped insecure retry for diagnosed certificate errors added at user request |
| Dependencies | Broad direct dependency ranges made tested versions unclear | Pinned direct runtime/test versions; resolved versions recorded; transitive dependencies are not fully locked |
| Evaluation | Unit tests alone did not test installed behavior or public contracts | Added real stdio transport tests, relocation/package check, synthetic transcripts, source hashes, and workflow evaluation specifications in evals/cases.json |
| Compatibility | New API shape differs from earlier draft | README calls out paginated list, renamed auth guidance, and expected ID inputs; legacy Kubernetes backend retained separately |

## Automated evidence

Run from an environment with requirements-dev.txt installed:

```powershell
python dell-devspaces-sandbox/scripts/validate_plugin.py
```

The validator writes validation/evidence/summary.json, pytest.xml, tool-contract.json, four protocol transcripts, and an installable ZIP. summary.json is the authoritative pass/fail record and includes source hashes, dependency versions, platform, and package SHA-256. Tests boot the ZIP after extraction outside the checkout. The release ZIP contains runtime files and review/spec documents; it excludes tests, local environments, connector binaries, credentials, and evidence archives.

The protocol transcripts are generated by deterministic test code driving real MCP initialize/list_tools/call_tool exchanges against a synthetic connector. They are **not model-driven eval transcripts**, and no prompt-selection pass rate is claimed. The limited secret-pattern scan is not a security certification.

The retained Kubernetes adapter has regression unit tests only. Its live SSO, API access, events/logs, and namespace discovery have not been revalidated in this review. Switching to it remains an explicit deployment choice.

## Required acceptance before release

1. **Live target and authentication:** non-empty inventory and cluster identity were verified using an existing session with the scoped TLS exception above. Fresh interactive authentication and approved CA provisioning remain acceptance items on a clean target; never treat an earlier empty inventory as proof of authentication or deletion.
2. **Live lifecycle:** start, inspect Running, stop, inspect Stopped, restart, inspect Running, then restore the agreed starting state. Do not perform delete unless a separate disposable deletion target is explicitly approved.
3. **Connection:** connect, verify the connector process and SSH alias, establish an actual Codex SSH session, disconnect only the owned tunnel, and prove the remote workspace remains running. Verify reconnect after workspace/MCP restart. Readiness text alone is not SSH evidence.
4. **Installed-client timeout/cancellation:** the current synchronous lifecycle tools can exceed Codex's documented default 60-second MCP timeout. Verify the installed client's effective timeout covers both transition waits and CLI overhead, including a delayed restart; verify the user can inspect state after a timed-out request. Client cancellation does not guarantee the CLI action was cancelled. Do not ship with this mismatch unresolved; an asynchronous operation API is an alternative if the host cannot support the required timeout.
5. **Fresh-chat evaluation:** install the package in an isolated test configuration, begin a new chat, and run evals/cases.json. Capture real tool arguments/results, final answer, latency, and transcript reference. Exercise explicit, implicit, misspelled, follow-up, ambiguous, wrong-cluster, gated, and negative prompts. A reviewer grades each case against its checks and the global rubric. Synthetic backend results validate sequencing without interrupting real workspaces; live cases validate the integration separately.
6. **Runtime/distribution:** confirm supported Python and approved connector installation on a clean target workstation, CA provisioning, connector integrity/distribution ownership, and supported OS versions. Only Windows/Python 3.12 is exercised here; do not advertise other platforms as verified.

## Plugin-directory gaps (separate from Git distribution)

This package is a local stdio integration requiring an external executable, network access to an organization-operated Dev Spaces cluster, and interactive local authentication. It has no hosted OAuth endpoint or registered app mapping. Public directory support and ChatGPT/mobile execution must be established with the intended deployment model; do not assume that a local Codex package is portable to those surfaces.

Before a public submission, obtain an approved support contact, applicable privacy/terms URLs, rights/approval for the listing assets and product naming, a supported review/demo account with realistic dummy workspaces, and surface-specific end-to-end evidence. Do not invent these administrative facts or expose real user credentials to satisfy a review checklist. The current user-requested display name is retained; formal product branding approval remains outside this code review.

## Known implementation limits

- The CLI's active authentication store can change outside this server. Preflight checks cannot eliminate a race caused by another process reauthenticating the same CLI account; use a dedicated local connector session and avoid concurrent external auth during operations.
- The CLI only selects by name, so duplicate names across namespaces are rejected rather than guessed.
- Pagination bounds model output; the CLI still retrieves its full account inventory internally.
- Graceful server exit closes owned tunnels. Abrupt process termination may leave local cleanup work.
- A timeout during stop can leave the workspace stopped or stopping. No automatic restart retry is made.

These limitations must remain visible in the skill/setup instructions and in release acceptance results.

## TLS retry follow-up

The user reported a real certificate-verification error during interactive login and explicitly authorized a TLS-disabled retry. Added DEVSPACES_ALLOW_INSECURE_TLS opt-in, runtime visibility, scoped auth guidance, and regression tests for default isolation, opt-in/restoration, and sanitized certificate errors. This is not authorization for a global or permanent TLS bypass. Successful interactive authentication, workspace discovery, and real SSH validation still require separate observed evidence.
