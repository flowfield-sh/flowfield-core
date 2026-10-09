# Changelog

## Unreleased

- Add installed Pi 1.1+ for the Coordinator and workers through native RPC, with automatic
  model discovery, provider-qualified choices, native continuation and shared Stop/setup.
- Load Pi extensions with native project trust, confirmation prompts and shutdown handling.
- Map compact, status, MCP and skills commands across Codex, Claude Code and Pi.
- Organize concise, consistent agent documentation under Agents.
- Upgrade stored harness settings to schema 52, preserving existing choices and history.
- Support Apple Silicon Macs and Linux arm64/x64; drop Intel Mac builds.
- Surface native Codex MCP tool approvals instead of silently rejecting worker stage
  updates and result submission. Decisions apply once to the exact active turn.
- Connect standalone Codex, Claude Code and Pi with the same `integration connect`, `status`
  and `disconnect` commands, preserving other native MCP connections.
- Preserve full public coordinator replies and retrieve saved history through scoped
  tools. Fresh-session handoffs use reported context headroom.
- Choose Coordinator and worker settings while adding a project. Preload saved models
  on project open/reconnect and show disabled loading controls during discovery.
- Give Needs you columns the board's independent scrolling and keep cards inside columns.
- Show concise tool names, command previews and working feedback; keep Send/Stop visible
  in narrow Coordinator panes. Simplify permission presentation and settings tabs.
- Show verified Claude model versions and native Auto/Bypass access modes where supported.
  Native tool access remains separate from code-delivery approval.
- Make CLI output readable by default, with explicit JSON output for automation, and
  accept human task/milestone references regardless of letter case.

- Use installed Codex directly through its app-server and installed Claude Code through
  the official Agent SDK, included with Flowfield. Remove separate ACP installs.
- Stop foreground turns and tracked native background work through native controls;
  preserve recovery holds when cleanup cannot be confirmed.
- Load models automatically when choosing a harness, simplify settings and selector
  copy, and show harness logos.
- Migrate existing launch records without dropping conversation or execution evidence.
  Revalidate prior adapter bindings before continuing a native conversation.
- Accept Codex's native Fast-tier confirmation when starting or resuming sessions,
  and avoid a false browser error after session recovery.
- Use matching harness/model menus, place Refresh models beside Save, and distinguish
  unchecked sign-in status from signed-out status.

**Upgrade:** stop Flowfield before updating. Schema-44 workspaces from 0.2.0/0.2.1
upgrade automatically through schema 52 after a verified snapshot. The queue stays
paused after restart. Older native session bindings require revalidation; uncertain
prompts are not replayed. No separate integration install is needed. Update project
guidance and restart standalone conversations. See [storage](https://docs.flowfield.sh/storage).


## 0.2.1

- Fix an unnecessary vertical board scrollbar when horizontal scrolling is needed.
- Keep code previews at a stable height so switching files preserves the task feed's
  reading position. File lists and diffs scroll independently and fill the preview frame;
  selecting another file starts its preview at the top.
- Correct coordinator guidance to use the current task preparation contract, removing
  obsolete decision-sequence instructions.

**Upgrade:** restart Flowfield after updating. Schema-44 workspaces remain compatible.
Update installed project guidance through project settings and start a fresh standalone
coding conversation to use the corrected instructions.

## 0.2.0

Plan, run and review work together with the Coordinator beside your board.

- Keep a persistent Coordinator conversation beside the board and selected task. Resume
  native conversation history, attach files, select models and access modes, and use
  supported native commands and Fast mode.
- Run the Coordinator and workers locally with scoped tools, saved permission answers
  and explicit stop/recovery controls.
- Show long command permission choices in full without interrupting the agent.
- Add an existing project through the directory picker and optionally preview/install
  guidance during adoption. Review required, prefilled project details before adding it.
  Ask the Coordinator to configure and validate worker setup.
- Follow task stages and streaming worker output in the feed. Ongoing output stays at
  the bottom; completed output remains with its attempt. View Current tokens below output.
- Run workers without the previous 15-minute execution cap; use Stop worker when needed.
- Give the built-in Coordinator the complete project toolset, including worker/queue controls,
  answers, feedback, result inspection, recovery and explicitly authorized chat approval.
- Confirm exact-result approval in the task composer, with an optional testing or approval
  comment, and request changes from the same task feed.
- Simplify navigation with a permanent project rail, persistent project views, task context
  beside the Coordinator and shared scroll boundaries. Refine tooltips, relative timestamps,
  task overrides, action ordering and review colors; remove routine session-resume notices.
- Scroll board columns independently with fixed headings and thinner native scrollbars.
  Show active workers against capacity and make queued retries reflect the queue state.
- Remove the Decisions feature. Keep durable technical guidance in the repository and
  use task conversations for questions, feedback and review.
- Open the ready local service in a browser for interactive launches; use `--no-open` to
  suppress this. Noninteractive launches print the URL.

**Upgrade:** 0.2.0 supports schema 44. Databases from 0.1.0 and earlier development builds
cannot be upgraded to it. Stop Flowfield, preserve your old workspace and use a separate
data directory for a fresh start. Existing schema-44 workspaces open unchanged.

Install the matching managed bridge with `flowfield harness install codex`; keep native
Codex installed and signed in. Update installed project guidance and start a fresh standalone
coding conversation afterward. MCP task preparation now uses `create_task`/`edit_task` with
`preparation`; result review and recovery replace the older run-review and manual integration
tools. The CLI uses `task prepare` in place of `task publish`. Restart Flowfield after upgrading;
the worker queue starts paused.
See the [installation](https://docs.flowfield.sh/installation) and
[storage](https://docs.flowfield.sh/storage) guides.

## 0.1.0

Initial Flowfield release: a shared task feed and workspace for agent software development.

- Adopt existing repositories and keep task intent, activity, questions, answers and results
  together in a durable conversation. Group tasks into milestones and track dependencies.
- Use the board and Needs you to prioritize work, answer questions and review results.
- Run independent tasks in isolated Git checkouts with configurable worker capacity and
  a paused-by-default queue. Codex is the first supported worker harness.
- Inspect and try an exact result, then explicitly approve delivery into the project checkout.
  Changed code or destination requires fresh approval; checkout blockers retain approval.
- Connect coordinators through MCP and installed project guidance. The CLI and web app use
  the same local service and persisted workspace.
- Preserve work through tested schema upgrades with verified database snapshots and guarded
  offline recovery. Existing supported Flowfield schemas 29 and 30 upgrade to schema 31.
- Share persistent notifications and dismissals across browsers and restarts. Check for
  compatible PyPI releases on web startup, hourly or manually, with release links and
  uv/pip upgrade instructions.

Install with `uv tool install flowfield-core`, or pip in a dedicated Python
environment. The package bundles its browser UI and agent guidance. macOS and Linux with
Python 3.12 are the release test targets; managed work requires an installed, signed-in
Codex CLI. Additional harnesses are planned.

**Upgrade:** stop Flowfield before changing versions and restart afterward. Supported database
migrations run automatically after a verified snapshot; the queue starts paused. Read the installation and storage docs
before upgrading and retain a full workspace backup for artifacts and disaster recovery.
