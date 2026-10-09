# Claude bridge and capability proof

This is development proof tooling. Claude is not yet a selectable Flowfield harness.
It reuses Flowfield's `AcpSession` and `LocalProcess` owners. The internal Claude
adapter requires an explicit development artifact. A verified standalone runtime can be
installed for integration checks; production role selection remains gated.

`proof.json` pins [claude-agent-acp 0.88.0](https://github.com/agentclientprotocol/claude-agent-acp/releases/tag/v0.88.0),
ACP SDK 1.7.0 and Claude Agent SDK 0.3.293. The measured host CLI is Claude Code
2.1.295, and the requested live-test model is `claude-sonnet-5-5`. Catalog discovery
does not establish authentication, model access or successful inference.

## Build

```sh
FLOWFIELD_BUILD_BUN=/absolute/path/to/bun node bridges/claude-acp/build.mjs
```

Use Bun 1.3.11 and Node/npm as build tools. An optional argument supplies the pinned
source archive. The builder verifies the archive/lock checksums and dependency pins,
uses `npm ci --ignore-scripts`, compiles TypeScript, runs the authentication regression
checks, and compiles a standalone executable for the current POSIX platform/architecture.
Generated source, dependencies and output stay in ignored `.work/`. The original Apache
license remains in that source tree. This is not a distributable installation bundle.

The current proof version is `0.88.0-flowfield.proof.4`; the recorded live lifecycle
and role trials used `.3`. Its authentication
patch, recorded in `build.mjs`, ensures the released CLI's `tokenSource: "none"`
must count as signed out. Upstream's subscription guard otherwise treats that truthy
string as a usable credential, accepting a signed-out session with `--hide-claude-auth`.
The patch also prevents the sentinel from concealing subscription billing. It preserves
upstream's credential classification; that classification does not prove entitlement
to use a billing route in a third-party integration.

The standalone entry requires an absolute `CLAUDE_CODE_EXECUTABLE`, suppresses raw
bridge wire logs, and disables Bun's project dotenv/bunfig/tsconfig/package autoload.
It uses the explicitly installed native CLI instead of the SDK's optional bundled CLI.
No separate Node or Bun runtime is needed for the compiled proof. Only macOS arm64
has been measured; other platforms need their own proof and eventual license/bundle checks.

## Installable runtime

```sh
FLOWFIELD_BUILD_BUN=/absolute/path/to/bun node bridges/claude-acp/build.mjs --runtime
FLOWFIELD_BUILD_BUN=/absolute/path/to/bun node bridges/claude-acp/package.mjs
flowfield harness install claude-code --bundle /path/to/bundle.zip --sha256 CHECKSUM
flowfield harness status claude-code --json
```

The runtime is `0.88.0-flowfield.1`. Its executable uses the user's installed,
unmodified Claude Code and needs no Node/Bun runtime. Each platform bundle contains
the executable, manifest, Flowfield license and third-party notices, retaining the
Agent SDK's own terms. The SDK is not Apache-licensed. Installation verifies pinned
metadata and checksums, bounds archive sizes, and selects an immutable generation;
it starts no service or native process and changes no native configuration/login.
Downloads require a compatible published release asset; local bundles support development.

The runtime enables the measured native cleanup protocol and a session-fenced
`_flowfield/sessionInfo` extension containing actual model identity and the native
policy-filtered catalog. It omits the proof's account diagnostics and native-control entry.
The catalog does not establish model access or inference. Default/alias model names
remain native choices; callers must verify the exact applied model before a prompt.

```sh
uv run scripts/check_claude_acp_scripted.py /path/to/bundle.zip --bundle
uv run scripts/check_claude_runtime.py /path/to/bundle.zip --native /absolute/path/to/claude
```

The first check uses a scripted native peer and sends no live model request. CI runs
its completion, foreground/background Stop, refusal and bridge-failure scenarios on
macOS/Linux arm64/x64. The second is an explicit signed-out, model-free native check
with isolated configuration and empty PATH. Real native compatibility is currently
measured only on macOS arm64; scripted platform checks do not establish it elsewhere.

The installed adapter consumes only model/session metadata, checks native offered
identity before each prompt, and omits lab usage bounds in ordinary runtime launches.
Exact native catalog discovery selects offered aliases without a prompt, resolves each
to its actual model and collects its effort/access controls. Inherited/default effort,
Fast, automatic permission classification, Plan transitions and unverified commands
are not offered. Changed model/access controls start a fresh native session; unchanged
Claude coordinator choices can resume the current session.
Unresolved aliases are omitted. Discovery must close with confirmed native cleanup;
durable service ownership/recovery of discovery remains required before selection opens.

Explicit role trials can test the original installed bundle with development query
bounds. They use exact Sonnet 5.5/low through native Claude Code, not a direct model client:

```sh
uv run scripts/check_claude_roles_live.py /path/to/bundle.zip --bundle \
  --native /absolute/path/to/claude --trial-root /private/trial/root \
  --role coordinator --invoke-live
```

Use `--role worker` for the run-scoped question path. These checkpoints do not replace
complete answer/result/delivery or human experience acceptance.

`--role continuity` requires `--codex-bundle /path/to/codex.zip` and
`--codex-native /absolute/path/to/codex`. It seeds explicitly simulated saved human
messages, then invokes five real coordinator turns: older history/full-text retrieval,
Claude resume, Codex switch, fresh Claude switch-back and a fresh access-mode change.
Claude prompts remain exact Sonnet 5.5/low; Codex uses Sol 6.1/medium. It verifies
retained native bindings and cleanup through both original installed bundles. It does
not supply a direct model API client or establish browser/human acceptance.

`--role journey --bundle` tests the complete three-attempt worker path with simulated
human input and review: question, saved answer and automatic fresh continuation, independent
candidate inspection, requested changes, exact approval and local delivery. It uses
ordinary native worker query settings rather than the cleanup proof's four-turn limit.
The fixture limits itself to three attempts and bounded waits; no personal service or
public repository is used. Model simulation does not accept the human experience.

`--role planned --bundle` adds actual coordinator task creation/preparation before
that worker journey, then verifies a fresh coordinator retrieves the delivered candidate,
saved human answer and requested changes through scoped project tools.

`--role parallel` requires the same explicit Codex paths as continuity. The actual
scheduler runs one Sonnet 5.5/low and one Sol 6.1/medium report task in independent
workspaces, verifies overlapping distinct native sessions, frozen role choices and
confirmed cleanup, and leaves the original checkout unchanged.

`--role controls --bundle` verifies current-turn uploaded UTF-8 text and PNG input,
then changes access mode to create a fresh native generation and Stops a bounded
foreground Bash helper. The live adapter owns termination; the fixture observes the
helper's exit independently and checks permission release and idle coordinator state.

## Explicit model-free native check

```sh
uv run scripts/check_claude_acp.py bridges/claude-acp/.work/claude-acp-proof \
  --native /absolute/path/to/claude
```

This opt-in command copies the artifact outside its build tree and runs it with empty
PATH, isolated HOME/user configuration and a disposable project. Native machine policy
remains native. It sends **no user prompt** and uses no personal credentials or project.
It checks bridge/native versions, catalog/effort metadata, native commands, a repeated
SDK initialization's empty background-task snapshot, an idle interrupt receipt, summary
context metadata and observed native-owner exit. The SDK probe uses only public control
methods and its documented spawn callback. It does not execute a tool or infer a response.

The shared ACP check verifies HTTP MCP registration input, model/mode/effort/Fast metadata,
an alternate native configuration directory, normal session close, rejection of an
unpersisted empty session on resume, signed-out auth rejection with the guard enabled,
and cleanup after an idle bridge is killed. A deliberately unreachable MCP endpoint
tests registration, not connection or authorization. Output is bounded and sanitized;
raw diagnostics and credentials are discarded. Native version mismatches fail the check.
All temporary user configuration is deleted after owned process-group shutdown.

## Measured limits

On macOS arm64, the standalone artifact opened/closed a real native session without
Node/Bun on PATH. The alternate `CLAUDE_CONFIG_DIR` was a **directory**, containing
`settings.json`; its Sonnet model/allowlist appeared in the ACP catalog. This is metadata
evidence, not applied live-model evidence. Exact native model validation while signed
out failed for lack of authentication. No live model was invoked.

The released bridge advertises close/load/resume, HTTP/SSE MCP and images. It exposes
native `mode`, `model`, `effort` and `fast` option IDs. Neither these advertisements nor
upstream's mocked tests establish a complete Flowfield journey. Images, public streaming,
usage, native guidance, permission/plan transitions, hooks, connected scoped MCP,
persisted-session continuity and actual foreground/background/subagent tools still
need end-to-end proof. Flowfield's current client does not advertise form elicitation;
the bridge disables native `AskUserQuestion` in that case. Durable Flowfield questions
must retain their existing scoped MCP path.

Session-close acknowledgment and native-owner/process-group exit are separate from
native-work cleanup. The default proof deliberately asserts `owned_work_stopped is None`:
the released bridge has no negotiated `flowfield.cleanup` receipt. The opt-in candidate
below adds one for measurement. Until its native tool ownership/termination is verified,
managed Claude launch must stay unavailable and unknown cleanup must continue to block
capacity. Neither proof claims containment of arbitrary daemons or external MCP services.

## Opt-in cleanup candidate

The development artifact enables its candidate extension only with the explicit
`--flowfield-proof-cleanup` argument. Draft ACP v2 is disabled in the standalone entry,
so every work-creating v1 route uses the same permanent fence. The bridge owns one
root session, snapshots public SDK messages before ACP translation, and observes each
native process through the SDK's documented spawn callback. Native-query replacement
or a second root cannot silently inherit the old owner's cleanup authority.

Cleanup seals requests, cancels the turn, waits for accepted requests to settle, requires
an interrupt receipt with no queued work, and calls `stopTask` for known native task IDs.
Fresh `reinitialize` background-membership snapshots must be empty twice with no intervening
work. It then closes query input and waits for the exact native child to exit, rejecting
late work during closure. There is no private SDK RPC, process scanning, history deletion
or second application lifecycle. Local/native settings and native account ownership remain
unchanged. Flowfield validates the exact session, scope, counts and observed-exit receipt
through its existing optional ACP cleanup callback.

Scope is `native-turns-and-tasks`, with a 10-second deadline, 256 tasks/termination
attempts and 100 observation passes. Missing snapshots/interrupt receipts, queued work,
unknown task types, query replacement, early native exit, active goals, unfinished hooks
or late activity produce `uncertain`. Accepted types are native local Bash and agent tasks.
Monitor and workflow termination has not been measured and remains unconfirmed.
Active goals have no verified pause control in the public SDK, so they stay uncertain.
Hook events are observed, but arbitrary hook descendants are not a containment claim.

```sh
node --test bridges/claude-acp/cleanup.test.mjs
uv run scripts/check_claude_acp.py bridges/claude-acp/.work/claude-acp-proof \
  --native /absolute/path/to/claude --cleanup
uv run scripts/check_claude_acp_scripted.py bridges/claude-acp/.work/claude-acp-proof
uv run scripts/check_claude_acp_scripted.py bridges/claude-acp/.work/claude-acp-proof --scenario foreground
uv run scripts/check_claude_acp_scripted.py bridges/claude-acp/.work/claude-acp-proof --scenario background
uv run scripts/check_claude_acp_scripted.py bridges/claude-acp/.work/claude-acp-proof --scenario refused
uv run scripts/check_claude_acp_scripted.py bridges/claude-acp/.work/claude-acp-proof --scenario bridge-failure
```

The native check sends no prompt: the candidate's empty-state receipt was measured on
macOS arm64 with CLI 2.1.295. The scripted check uses the actual built bridge and actual
SDK against a fake native peer. It verifies public/private output separation, image input,
permission replies, model/effort controls and injected MCP configuration. Foreground/
background scenarios use an actual sleeping test process with a separate live `LocalProcess`
owner outside the bridge group; confirmed receipts require its observed exit. Refused
cleanup and bridge failure deliberately leave that process alive with an unconfirmed
receipt; the test's live owner subsequently stops it. No persisted PID is used to
recover ownership and no native Claude/model is invoked by this scripted probe.

Ordinary checks run only controller/receipt tests, not the native/scripted commands.

The internal `ClaudeAgent` uses the shared ACP activity, attachments, permission and Stop
owners. Its startup metadata selects the exact native model, effort and access mode; it
checks the candidate's session-fenced actual native model before every prompt. The candidate
currently bounds inference to four native turns and a $0.50 native estimated budget per
query. These are development proof bounds, not application defaults or billing guarantees.
Same-session model changes, optional effort and native commands remain unverified. The
application selection owner rejects Claude launches/discovery unless proof code supplies
the explicit artifact; API settings validation retains that gate. Integrated scripted role
tests exercise the actual coordinator/supervisor and scoped HTTP MCP owners without models.

The explicit service-loss probe starts an independent fixture service and interrupts its
live `LocalProcess` owner while a native Bash helper is running. A fresh Supervisor opens
the retained state: coordinator turns stay uncertain, worker capacity remains reserved,
and no native prompt is replayed. A helper's observed exit is not promoted to a native
cleanup receipt. Saved PIDs are never termination authority. The helper expires after
75 seconds; no personal service is started or reset.

```sh
uv run scripts/check_claude_restart_live.py bridges/claude-acp/.work/claude-acp-proof \
  --native /absolute/path/to/claude --trial-root /dedicated/private/trials \
  --role worker --invoke-live
```

Use `--role coordinator` for the coordinator loss/recovery boundary. These commands
invoke exact Sonnet 5.5/low through native Claude Code and remain outside ordinary CI.
These scripted results establish integration mechanics; the explicit native trials below
measure actual tool, hook, subagent and persisted-session behavior. Integrated application
scope/service-restart journeys and goals/Monitor/workflows remain unverified.

## Explicit native Sonnet trials

`scripts/check_claude_acp_live.py` requires `--invoke-live` and an existing private trial
root outside the checkout and shared temporary directories. It uses native authentication,
collects no credentials and supplies exact `claude-sonnet-5-5` before startup. The lab-only
`_flowfield/proofStatus` reads the public SDK context summary and selected nonsecret account
metadata; every prompt requires an exact model match first. Post-turn model usage must also
match. Only selected init/result/task metadata is exposed to the proof observer, excluding
assistant/thought messages. Raw native errors/logs are not retained. Native account fields
can be absent and must not be treated as billing/subscription eligibility evidence.

```sh
uv run scripts/check_claude_acp_live.py /absolute/path/to/claude-acp-proof \
  --native /absolute/path/to/claude --trial-root /private/trial/root \
  --invoke-live --scenario smoke
```

Available scenarios: smoke, foreground, background, bridge-failure, continuity, mcp,
subagent and hook. On macOS arm64/CLI 2.1.295 the native Sonnet smoke, foreground Bash,
background Bash, bridge-failure uncertainty, persisted session recovery, authenticated
HTTP MCP fixture, nested subagent Stop and UserPromptSubmit hook cancellation passed.
The subagent's ACP turn stays pending while its background work runs; parent result alone
is not completion. Hook cancellation ran before model inference and reported zero usage.
These are harness proofs, not service-managed role or browser acceptance.

Tool permissions accept only the bounded fixture command or the specific fixture MCP/
Sonnet agent request. The helper expires after 90 seconds; its live-run marker PID is
observed for lifetime only, never used for cleanup or recovery. Confirmed cleanup must
also observe that test process gone, independently of bridge-group exit. Per-query limits
are four turns and a $0.50 native usage budget; subagents are pinned to the same exact model.
Reports/state stay in the private trial directory. No fixture initializes a user project
or starts the user's Flowfield service. Ordinary checks never invoke these scenarios.

Anthropic's [Agent SDK authentication guidance](https://code.claude.com/docs/en/agent-sdk/overview)
documents API/provider authentication and requires prior approval for third-party products
offering claude.ai login/rate limits. Flowfield owns no account switching or credentials.
The bridge/SDK already launches the installed, unmodified Claude Code binary; it is not
a direct model Client SDK integration. The separate
[Claude Code guidance](https://code.claude.com/docs/en/legal-and-compliance#authentication-and-credential-use)
explicitly preserves end-user sign-in to that binary with their own subscription, including
platform-hosted instances. Establish how the intended native-client wrapper fits both
documents before claiming supported billing; neither blanket API-key-only eligibility nor
permission inferred from a bridge login method/token is established by these probes.
