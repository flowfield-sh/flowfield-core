# Claude capability proof

This is development proof tooling. Claude is not yet a selectable Flowfield harness.
It reuses Flowfield's `AcpSession` and `LocalProcess` owners; it adds no application
settings, adapter, service or runtime installation.

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

The proof changes the bridge version to `0.88.0-flowfield.proof.2`. Its authentication
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
or late activity produce `uncertain`. Current known types are native local Bash, agent,
monitor and workflow tasks; their actual stop semantics still need Claude-model proof.
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
These results establish the integration mechanics, not actual Claude tool, hook, goal,
subagent, persisted-session or service-restart behavior. H1.1 remains open.

Anthropic's [Agent SDK authentication guidance](https://code.claude.com/docs/en/agent-sdk/overview)
documents API/provider authentication and requires prior approval for third-party products
offering claude.ai login/rate limits. Flowfield owns no account switching or credentials.
Establish the supported native authentication/billing route before live Sonnet checks;
the bridge exposing a login method or accepting a token is insufficient evidence.
