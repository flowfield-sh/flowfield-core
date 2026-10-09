# Harness adapters

Application records use Flowfield task and execution IDs. Harness session IDs,
transport, model capabilities, event translation and process termination stay at
this boundary; the service owns assignments, input delivery, approval and integration.

- `codex_connection.py` configures/probes the coordinator's native MCP connection.
  Connectivity does not prove a fresh coordinator followed project guidance, dispatch
  workers or wake an inactive conversation.
- `codex_agent.py` resolves the installed standalone bridge and native choices over ACP.
  Local workers use native coding tools and a revocable, run-bound Flowfield MCP endpoint.
- `claude_agent.py` launches installed, unmodified Claude Code through its standalone
  ACP/Agent SDK bridge. Exact native model identity is confirmed before dispatch;
  Default/Accept edits retain native semantics. Plan, Fast and commands are not exposed.
  `agent_selection.py` owns both roles and project-bound discovery for either harness.
- `local_execution.py` prepares Local attempts. Git adapters own
  worktrees, candidate checks and delivery. The supervisor reserves work, freezes input,
  starts workers and reconciles recovery through the shared application operations.

The ACP path separates `GitWorkspace` (checkout and result capture),
`LocalHost`/`LocalAttempt` (explicit host environment and per-attempt scratch state),
and `LocalProcess` (owned POSIX process groups). Local is the automatic environment
for managed execution, result validation and inspection copies.

Model turns have no elapsed-time deadline. Explicit stop, transport failure and service shutdown
still use the shared cancellation/native-cleanup/session/process-exit budgets; setup and
validation retain their command timeouts. The former fixed 15-minute Codex turn cap is removed
for both worker and coordinator sessions.

`LocalHost` preserves the supplied HOME, PATH and harness configuration, without a
tool inventory or mandatory language runtime. Its input is the intended launch
environment, not necessarily the service's activated virtual environment or an
interactive terminal's PATH. Do not persist its credential-bearing values. Each
attempt receives its own worktree, TMPDIR/TMP/TEMP, runtime directory and run identity;
project setup still owns dependencies, ports and test data. Shared mutable services
need distinct resources or serialized work. Native harness restrictions remain native.

ACP uses the same `LocalProcess` cleanup implementation as local subprocess tests.
The caller drains process streams and confirms its harness turn/tool lifecycle before
capturing a result. A process-group receipt does not prove detached descendants exited,
and a retained PID alone cannot establish process ownership after restart. Retain work
and uncertain execution until reconciliation; never kill processes by name. Worktrees
share Git metadata. Local capture allows commits descending from the original baseline
only in the detached checkout, then creates the reviewed result from that original parent.
A changed checkout binding, branch or unrelated ancestry fails explicitly. Historical commits remain unchanged. Native tools must not modify shared branches/refs.

Managed answers continue through service-owned attempts, preserving assignment and answer
bindings. Saved input, reservation and execution remain distinct facts. Worker continuation
uses preserved workspaces and deliberate context; it does not promise a persistent harness
session. The browser reads application activity, never provider messages directly.

The embedded coordinator uses the same ACP adapter with scoped planning tools and its saved
Access mode. It runs in the registered project directory and resumes the native session across
turns and service restarts. Application history remains durable; explicit recovery from a missing
session uses a bounded handoff instead of claiming to replay the complete native history.
Harness or changed Claude control choices start a fresh retained native generation with
a frozen, bounded Flowfield handoff; unchanged Claude choices resume. Events, permissions
and tool grants are fenced by the exact turn/generation. Older public evidence is available
through scoped source reads; native transcript conversion and permission transfer are absent.
Mid-run steering remains future work. Deterministic adapters test application behavior without model calls.
The installed service requires no Node runtime.

Local setup, validation and inspection use the same host/tooling model. Saved inspection
launchers inherit the human terminal environment and contain only copy-specific paths,
never a serialized credential-bearing service environment. Commands are trusted project
configuration, with bounded output and owned group cleanup; they must not daemonize.
Native cleanup uncertainty stays distinct from group exit. Recovery never signals a new
Local attempt from a persisted PID; unconfirmed native ownership keeps capacity reserved.

Permission projections retain at most 32 public tool details per turn, each bounded to
16,000 characters. Codex adds known command/cwd/permission facts; unrelated raw inputs,
metadata and reasoning stay excluded. ACP context occupancy is not billable token usage;
unsupported input/output totals remain unknown. Catalog discovery is shared by concurrent
callers, cached for five minutes (with explicit refresh), bounded to 64 model selections and
120 seconds, and starts no model turn. Public tool activity merges partial updates in a
100-entry cache bounded to 4,000 characters per field; caches clear when the turn ends.

Shutdown budgets are separate for turn cancellation, native cleanup, session/transport
closure and process exit. Codex allows 15 seconds for its bridge's 10-second native
cleanup operation. Other phases default to five seconds; process escalation can spend
that budget on each of TERM, KILL and reaping. After acquiring a started process owner,
the sequential phase budgets total at most 50 seconds for Codex (excluding event-loop
starvation or callbacks that ignore cancellation). A cancelled caller does not cancel
shared shutdown, and a late or missing receipt never becomes confirmed termination.
