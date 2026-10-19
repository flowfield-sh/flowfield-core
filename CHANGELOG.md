# Changelog

## Unreleased

### Features

- Plan and deliver work as tasks, with optional internal subtasks and local dependencies. One task owns the combined result, reviewer sign-off and final delivery; code still requires exact approval.
- Create file and report tasks without a project. Browse retained outputs by task and folder in Files, with compact navigation and a file inspector.

### Improvements

- Prepare and execute tasks directly from their agreed requirements, without maintaining a separate progress checklist. Worker activity, checks and reviewer sign-offs retain the execution history.
- Follow purpose, progress, questions and results on one task page. Internal work stays disclosed, and worker and reviewer overrides live in task Settings.
- Open code results with a directly visible, copyable launch command. Document editors have their own links, and task feeds load the full event list with deliberate expansion.

### Bug fixes

- Keep model selection explicit when navigating to Tasks; opening a page no longer opens the Coordinator model selector automatically.
- Keep completed tasks and their delivered results stable when prerequisite work changes later, without unnecessary warnings or downstream reconciliation.

### Breaking changes

- Milestones and their separate board, review and delivery controls are replaced by workspace tasks. Task APIs, MCP tools and CLI commands use workspace identity; older task and milestone routes are removed.
- Older Flowfield databases are no longer migrated. Start with a fresh data directory when the schema differs; existing databases are rejected without automatic deletion. The pre-upgrade database backup and restore commands have been removed. See [local storage](https://docs.flowfield.sh/storage).

## 0.5.0

Bring related repositories into one workspace, with a shared Coordinator, plan and knowledge.

### Features

- Create workspaces for multiple existing repositories. Plan through one Coordinator conversation and follow shared Board, Inbox, milestones, labels and worker controls while each project keeps its own code-delivery destination.
- Link task dependencies across projects in the same workspace. Workers receive the relevant prerequisite evidence while their implementation and approval stay scoped to one project.
- Maintain shared, versioned documents with revision history and restoration. Select required sources and reference material for each project or task; prepared work retains exact revisions and shows when newer sources are available.
- Replace fixed task types with optional workspace labels, and keep project, milestone and label filters in the URL for navigation and sharing.

### Improvements

- Give workers clearer, state-specific handoffs for implementation, answered questions and requested changes, with resolved prerequisite details and task-focused source selection.
- Recover a normally finished worker turn that omitted its result or blocking question with one visible reminder in the same session. Code still needs exact human approval. See [following an attempt](https://docs.flowfield.sh/tasks#follow-an-attempt).

### Bug fixes

- Correct dependency readiness and status across repositories so unrelated Git histories do not block downstream work.
- Make planning-question answers and document lifecycle feedback clearer, and keep Coordinator navigation usable on narrow screens.

### Upgrade

Stop Flowfield before updating, then restart it. Supported installations from 0.2.0 through 0.4.0 upgrade automatically after a verified database snapshot. Each existing project gets its own **Project name Workspace**, preserving its conversation, settings, milestones and work history. Existing projects are not combined automatically; moving them between workspaces is not yet supported. See [storage and recovery](https://docs.flowfield.sh/storage).

Saved queue choices are retained. Pause queues before shutdown if you want to review work before new starts resume; interrupted workers still require recovery. Update installed Coordinator guidance and reconnect standalone agent sessions after upgrading.

Workspace ownership changes the CLI, API and MCP interfaces for coordination, milestones, labels and worker controls. Update scripts and integrations to use workspace IDs and explicit project destinations for task work. See the [CLI reference](https://docs.flowfield.sh/cli) and [standalone agent setup](https://docs.flowfield.sh/agents/overview#standalone-conversations).

## 0.4.0

A clearer workspace for starting projects, following work, and managing your Flowfield installation.

### Features

- Receive linked Inbox alerts while using Flowfield and optional system notifications while elsewhere. Alerts open the relevant question, permission request or result; bursts are grouped and delivery is coordinated across tabs.
- Configure announcements and browser permissions in Settings → Notifications, with a test notification action. Manage update checks in Settings → Updates, with release notes, upgrade instructions and the latest check status.
- Keep each project's Run/Pause choice across service restarts, with the same queue control available in Project settings → General.

### Improvements

- Start from a Home page listing your projects, edit General settings directly, and get an introductory Coordinator message to help begin planning.
- Find questions, reviews and interventions under Inbox, formerly “Needs you.”
- Use consistent, dismissible feedback for saves, actions and failures, including inside dialogs. Settings group configuration, observed status and recovery actions more clearly.
- Use Verify installation to detect and check a harness in one action. Results include their check time and survive page reloads while the service is running. Codex and Claude Code checks cover version and native sign-in; Pi checks its version.
- Reset task worker model settings to project defaults directly beside Save, with the updated settings remaining open for review.

### Bug fixes

- Improve connection, timeout and interrupted-save feedback while retaining contextual recovery controls.
- Fix notification permission feedback, alerts suppressed for items outside the visible area, and duplicate announcements during manual update checks.

### Upgrade

Stop Flowfield before updating, then restart it. Workspaces from 0.2.0 through 0.3.1 upgrade automatically after a verified database snapshot. See [storage and recovery](https://docs.flowfield.sh/storage).

Enabled queues can start eligible work when the service returns. Pause queues before shutdown if you want to review work before it resumes. Interrupted workers still require recovery.

The notification bell, saved notification history and legacy notification-history API are removed. Pending work remains in Inbox, and update information moves to Settings → Updates. Existing notification history is discarded during the upgrade; projects, conversations and execution evidence are preserved. Enable optional system delivery in Settings → Notifications.

## 0.3.1

### Bug fixes

- Preserve Claude Code's native sign-in when using default or custom configuration directories, and report failed Claude Code and Pi turns correctly.
- Show actionable sign-in, provider, model-access and usage-limit recovery messages across Codex, Claude Code and Pi.
- Load larger Pi model catalogs when multiple providers are configured.

### Improvements

- Refresh harness detection after installation, report failed setup checks clearly, and distinguish Pi installation checks from provider access. See [harness setup and recovery](https://docs.flowfield.sh/agents/overview#setup).

### Upgrade

Stop Flowfield before updating, then restart it. Existing workspaces remain compatible.

## 0.3.0

Use Codex, Claude Code or Pi for your Coordinator and workers, with independent choices for each role and native accounts, models and settings.

- Run installed Codex and Pi directly and Claude Code through the bundled official Agent SDK. Harness support needs no separate installation.
- Load Pi extensions, custom tools and MCP settings with native project trust; show extension confirmations and honor shutdown hooks. Pi uses Full access; terminal widgets and selection/text-entry dialogs require its terminal interface.
- Support `/compact`, `/status`, `/mcp` and `/skills` across all three harnesses. Standalone conversations share `integration connect`, `status` and `disconnect`. Handle large Codex MCP inventories without losing the native connection.
- Stop active turns and supported native background work. Keep recovery holds when cleanup cannot be confirmed, and never replay interrupted prompts automatically.
- Choose project agents during creation, load models automatically and preload saved choices on reconnect. Simplify settings, matching selectors and harness logos. Show resolved Claude model versions and native Auto/Bypass modes where supported.
- Preserve full public Coordinator replies and make saved history available through scoped tools. Continue compatible native sessions across service restarts; fix Codex Fast-session settings and session-recovery errors.
- Show readable tool names, command previews and working feedback. Keep Send/Stop visible in narrow Coordinator panes; surface native Codex MCP approvals for the exact live turn. Tool permissions remain separate from approval to deliver worker code.
- Give Inbox columns independent scrolling and contain cards within columns. Make CLI replies readable by default, retain explicit JSON output, and accept task/milestone references regardless of letter case.
- Standardize agent documentation under Agents and consolidate shared connection guidance.
- Support Apple Silicon Macs and Linux arm64/x64. Intel Mac builds are discontinued.

**Upgrade:** stop Flowfield before updating. Workspaces from 0.2.0/0.2.1 upgrade automatically after a verified database snapshot, preserving settings, conversations and execution evidence. The queue starts paused. Older native session bindings need revalidation. Previously shortened replies retain only the text that was saved. Update installed project guidance and restart standalone conversations. See [storage](https://docs.flowfield.sh/storage) for backup and recovery.

## 0.2.1

- Fix an unnecessary vertical board scrollbar when horizontal scrolling is needed.
- Keep code previews at a stable height so switching files preserves the task feed's reading position. File lists and diffs scroll independently and fill the preview frame; selecting another file starts its preview at the top.
- Correct coordinator guidance to use the current task preparation contract, removing obsolete decision-sequence instructions.

**Upgrade:** restart Flowfield after updating. Schema-44 workspaces remain compatible. Update installed project guidance through project settings and start a fresh standalone coding conversation to use the corrected instructions.

## 0.2.0

Plan, run and review work together with the Coordinator beside your board.

- Keep a persistent Coordinator conversation beside the board and selected task. Resume native conversation history, attach files, select models and access modes, and use supported native commands and Fast mode.
- Run the Coordinator and workers locally with scoped tools, saved permission answers and explicit stop/recovery controls.
- Show long command permission choices in full without interrupting the agent.
- Add an existing project through the directory picker and optionally preview/install guidance during adoption. Review required, prefilled project details before adding it. Ask the Coordinator to configure and validate worker setup.
- Follow task stages and streaming worker output in the feed. Ongoing output stays at the bottom; completed output remains with its attempt. View Current tokens below output.
- Run workers without the previous 15-minute execution cap; use Stop worker when needed.
- Give the built-in Coordinator the complete project toolset, including worker/queue controls, answers, feedback, result inspection, recovery and explicitly authorized chat approval.
- Confirm exact-result approval in the task composer, with an optional testing or approval comment, and request changes from the same task feed.
- Simplify navigation with a permanent project rail, persistent project views, task context beside the Coordinator and shared scroll boundaries. Refine tooltips, relative timestamps, task overrides, action ordering and review colors; remove routine session-resume notices.
- Scroll board columns independently with fixed headings and thinner native scrollbars. Show active workers against capacity and make queued retries reflect the queue state.
- Remove the Decisions feature. Keep durable technical guidance in the repository and use task conversations for questions, feedback and review.
- Open the ready local service in a browser for interactive launches; use `--no-open` to suppress this. Noninteractive launches print the URL.

**Upgrade:** 0.2.0 supports schema 44. Databases from 0.1.0 and earlier development builds cannot be upgraded to it. Stop Flowfield, preserve your old workspace and use a separate data directory for a fresh start. Existing schema-44 workspaces open unchanged.

Install the matching managed bridge with `flowfield harness install codex`; keep native Codex installed and signed in. Update installed project guidance and start a fresh standalone coding conversation afterward. MCP task preparation now uses `create_task`/`edit_task` with `preparation`; result review and recovery replace the older run-review and manual integration tools. The CLI uses `task prepare` in place of `task publish`. Restart Flowfield after upgrading; the worker queue starts paused. See the [installation](https://docs.flowfield.sh/installation) and [storage](https://docs.flowfield.sh/storage) guides.

## 0.1.0

Initial Flowfield release: a shared task feed and workspace for agent software development.

- Adopt existing repositories and keep task intent, activity, questions, answers and results together in a durable conversation. Group tasks into milestones and track dependencies.
- Use the board and Inbox to prioritize work, answer questions and review results.
- Run independent tasks in isolated Git checkouts with configurable worker capacity and a paused-by-default queue. Codex is the first supported worker harness.
- Inspect and try an exact result, then explicitly approve delivery into the project checkout. Changed code or destination requires fresh approval; checkout blockers retain approval.
- Connect coordinators through MCP and installed project guidance. The CLI and web app use the same local service and persisted workspace.
- Preserve work through tested schema upgrades with verified database snapshots and guarded offline recovery. Existing supported Flowfield schemas 29 and 30 upgrade to schema 31.
- Share persistent notifications and dismissals across browsers and restarts. Check for compatible PyPI releases on web startup, hourly or manually, with release links and uv/pip upgrade instructions.

Install with `uv tool install flowfield-core`, or pip in a dedicated Python environment. The package bundles its browser UI and agent guidance. macOS and Linux with Python 3.12 are the release test targets; managed work requires an installed, signed-in Codex CLI. Additional harnesses are planned.

**Upgrade:** stop Flowfield before changing versions and restart afterward. Supported database migrations run automatically after a verified snapshot; the queue starts paused. Read the installation and storage docs before upgrading and retain a full workspace backup for artifacts and disaster recovery.
