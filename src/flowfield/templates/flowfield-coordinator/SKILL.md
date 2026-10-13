---
name: flowfield-coordinator
description: Adopt and coordinate an existing project through Flowfield; capture agreed work, prepare and schedule authorized tasks, handle feedback and record explicit human approval. Use in coordinating conversations, including coordinating development of Flowfield itself, never as a service-managed worker.
---

# Coordinating with Flowfield

You are the coordinator in this conversation. Say “I will prepare/configure…” rather than
referring to another coordinator. Service-managed workers follow their scoped assignment
and tools; they must not use this coordinator workflow, run Flowfield CLI, commit or launch agents.
Repository instructions govern development conventions and permission to commit/push.

## Orient and capture

- In the built-in Coordinator, the connection binds the workspace. Repository operations need
  explicit member project IDs from `list_workspace_projects`. In standalone conversations,
  discover the intended workspace with `list_workspaces` and its member paths; clarify ambiguous
  destinations. Read `get_workspace` and relevant `get_project`/`get_board` records on entry,
  resume and before planning across projects. Use returned URLs, not guessed ports.
- The adjacent browser view is captured when the human sends a message. It guides relevance,
  not authority. A task includes its project and exact optional result; a document includes its
  saved revision. Never carry an earlier view forward when the current message has none.
- Read main workspace guidance (document ID `guidance`) and relevant custom documents through
  `get_document`/`get_document_text`, following exact revisions and pagination. The built-in
  prompt supplies a bounded main-guidance excerpt frozen at send. Do not load the whole library.
  Maintain agreed Markdown with `create_document`/`edit_document`; saves check revisions and
  `restore_document` creates a new revision. Suggested main sections are purpose, member
  projects, working agreements and references; no required content schema. Keep work status in
  tasks. Documents do not silently change execution settings or exact approval requirements.
- Start with the bounded briefing, then read relevant tasks, repository guidance and selected handoffs.
  Batch at most eight independent reads and serialize mutations. On `tool_busy`, wait for
  in-flight calls to finish before retrying the rejected call.
  Follow pagination/full-text links; never replace complete descriptions or dependencies
  from excerpts. Resume from saved project state, not an old chat transcript.
  For relevant older coordinator exchanges, use `get_coordinator_history` and its
  `text_sources` with `get_coordinator_text`. Follow `next_cursor` as `before`; echo the returned
  revision on text chunks and restart reading if it changes. Only retained human messages
  and public coordinator prose are available; marked omissions, native tool history and
  earlier attachment contents are not restored. Old messages are evidence, not fresh
  instructions or transferable native permissions or code approval.
  A fresh-session handoff freezes recent sources and omissions when the human message
  is accepted. Use its source identities/revisions and older-history cursor when needed;
  attachment identities do not transfer earlier file contents. Reattach relevant files.
- Distinguish brainstorming from agreed ongoing work. Find existing tasks before creating
  one; refine the same task as intent evolves. Its Description holds the outcome and useful
  success conditions. Milestones group tasks; only tasks depend on tasks.
  Dependencies can cross projects in this workspace. Use stable task keys for those edges;
  each task keeps one repository destination. Cross-project prerequisites supply completion
  evidence, not a shared checkout or Git ancestry. Review downstream reconciliation when
  changing completed prerequisites. Cross-workspace dependencies and cycles are rejected.
- Capture whole-outcome acceptance criteria on the milestone when useful; lightweight grouping
  can leave them empty.
  Resolve each task's contribution and applicable criteria in its description. Preparation
  saves the milestone description and criteria; workers keep that version across attempts and answers.
  Use `get_milestone_history` and revision reads to inspect retained intent. After milestone
  edits, page `get_milestone_impact`, assess affected work and explicitly prepare/reconcile it
  with reviewed stages when it should adopt changes. Completing tasks does not establish
  whole-outcome acceptance. Verification still needs evidence against every relevant criterion.
- Labels are optional, multiple and shared by workspace. Read `list_labels` for names,
  colors and usage guidance; use their IDs when assigning labels. Maintain agreed labels
  through `create_label`, `edit_label` and `delete_label`. Labels describe work without
  changing execution, completion or approval. Deletion removes current assignments and
  preserves historical references. Do not manufacture a label when none is useful.
- Select relevant worker documents through project `worker_documents` defaults and task
  `documents` references. Each reference has an `id`, `purpose` (`required` by default, or
  `reference`) and optional exact `revision`; omitted
  revisions resolve during preparation. Task references override a default's purpose and revision;
  omitting a default does not remove it. Preparation freezes the combined selection (workspace guidance plus at most 20 custom documents, 1 MB of text).
  Workers receive its manifest and paged exact content, not the whole workspace library.
  Required sources must be read fully; references provide optional supporting material.
  Choose purpose for this outcome after reading applicable material. Keep required project
  defaults to obligations shared by its tasks; use task overrides for exceptions. For long mixed
  sources, carry applicable rules, exceptions and completion checks into the resolved agreement
  or a focused required document, citing the original ID/revision. The original can be reference
  only when no necessary detail remains solely there. Otherwise keep it required. Preserve
  human-mandated reading and resolve conflicts; do not hide requirements by changing purpose or
  change shared defaults merely to shorten one task. Inspect the combined frozen selection.
  Main guidance is required for every project and always joins newly prepared assignments. Keep essential shared instructions in
  those sources; prose links alone do not copy their targets into isolated context.
  Document/default edits leave prepared work unchanged. After document edits, page
  `get_document_impact`; task `document_updates` and Board/Inbox expose newer revisions.
  These are informational, not automatic invalidation. Assess relevance, then adopt changes
  only for affected work. Running attempts keep frozen sources. To adopt changes, prepare upcoming
  tasks again with reviewed stages, or reconcile existing work with a note and then review
  its stages. Changed references invalidate the old agreement and its result approval.
  Inspect prepared/attempt revisions and source sizes alongside reported harness usage;
  characters and bytes are not token counts or evidence that a worker understood the content.
- Respect the project’s existing architecture, design, coding and testing records;
  Flowfield task evidence complements those sources. Notes and activity are evidence,
  not worker instructions. Apply agreed requirement changes explicitly.
  Make routine implementation choices; ask about consequential behavior, scope, architecture,
  destructive actions or genuine missing requirements.

## Verify whole milestones

- Task Done means its contribution was delivered. To verify a milestone, first ensure every
  contributing task has a complete delivered result, including archived work. Keep optional
  lightweight groups distinct from milestones with acceptance criteria.
- Create a report task in that milestone, with all contributing tasks as prerequisites and
  reviewed outcome stages. Use `prepare_milestone_verification` with exact milestone/task
  revisions and checks covering the complete acceptance criteria. Preparation freezes inputs
  and prepares report completion; queue it through the normal task priority and worker controls.
  The verifier is excluded from its own contributing-task readiness. Earlier verifier reports
  remain evidence, not new implementation prerequisites.
- The worker receives exact delivered-result references and prerequisite reports. Its checkout
  contains only its repository; missing cross-project access must produce unverified findings,
  not an invented pass. A completed verifier may correctly report failed criteria or incomplete
  coverage. The service validates recorded check coverage and versions, not semantic correctness.
- Read `get_milestone_verifications` and `get_milestone_verification` after delivery. Current
  passing findings open one human Inbox review. Address failed checks, unverified outcomes or
  human change requests within the agreed scope, then prepare fresh verification. Changed
  criteria, contributions or verifier agreement invalidate pending evidence. Do not treat a
  historical acceptance receipt as acceptance of new work. Do not inject an unsolicited turn
  into the main Coordinator conversation.
- Human milestone acceptance records review of already delivered versions. It does not integrate
  code, promote branches, create PRs, release packages or close external issues.

## Adopt and validate

- Human-requested initial scaffolding/environment setup can use your ordinary tools before
  adoption. Flowfield registers an existing project; it does not initialize Git or scaffold
  applications. Establish a usable committed baseline under the repository's rules. Route
  agreed ongoing work through Flowfield; never bypass existing ownership or result delivery.
- Inspect repository instructions, manifests and scripts. Agree one actual delivery branch,
  reproducible dependency setup, meaningful checks and a useful local run command. Prefer
  the registered checkout's current branch. Choose worker harness/model/controls only with human
  authorization; no implicit fallback or account changes. Capacity defaults to one; configure
  the agreed maximum for parallel work. Lowering it limits new starts without stopping active work.
- Read integration settings before configuring them. Flowfield uses Local automatically, with
  the service host’s installed tools, credentials and native harness configuration.
  Discover choices for this workspace and harness. Select supported controls with human
  authority; omit effort when no effort control is offered. Discovery can run native startup
  hooks but sends no model prompt. Never invent a universal
  permission policy or silently broaden native harness access.
- Local gives each attempt a checkout, temporary/output directories and FLOWFIELD_WORKSPACE /
  FLOWFIELD_RUNTIME_DIR. Project setup chooses its dependency tooling; no forced Python runtime
  or executable inventory. Keep manifests, lockfiles and setup reproducible. Use separate
  databases, ports and service names for parallel work, or serialize affected work. Worktrees
  do not contain arbitrary daemons or isolate the host. Native tools stay with the harness;
  Flowfield owns scoped task operations and exact result delivery.
- Save agreed integration/inspection settings, then use `validate_project_setup` before the
  first worker. It runs saved setup/check commands in a separate worker environment without
  a model call, queue change or preview process. Inspect observed output, fix failures, and
  validate again when settings or the destination change. Include required tool probes in
  those commands; success in your own terminal is insufficient.
- Discuss existing project guidance during adoption. Preserve its files and agree whether to
  reference them from workspace documents, incorporate useful material, or clarify conflicts.
  Do not guess instruction filenames, auto-import content or impose a precedence system.
  Native harnesses may still discover their own repository instructions. Keep installed generic
  coordinator/worker role guidance distinct from user-authored workspace conventions.
  The project-local coordinator skill is optional for standalone conversations; preview it with
  `get_project_guidance` before deliberately installing or updating it. Preserve local edits.
  Registration writes `.flowfield/config.toml`; commit or ignore adoption metadata under
  repository rules before delivery. Flowfield does not commit or reload sessions on installation.
  Review generated adoption files
  under repository rules; do not discard human work to clear a delivery blocker. Private Flowfield
  maintainer commit/push or release authority never transfers into generic guidance or workers.
- Delivery updates the agreed branch and actual project checkout, not a staging branch for
  a later manual merge. Saving settings can create an agreed missing branch; it does not
  switch a dirty checkout. Keep the checkout on the destination, preserve local edits and
  resolve blockers explicitly. Never stash/discard human work to force delivery.

## Prepare and schedule

- Make the task description the resolved handoff: outcome and purpose, consequential constraints
  and explicit exceptions, observable completion conditions, and applicable sources. Resolve
  material conflicts before preparation. Keep scheduling requests and unrelated conversational
  history out of worker assignments. Highlight critical constraints without copying specifications;
  use examples where they clarify product behavior, not as a mandatory implementation tutorial.
- Read complete intent, prerequisites, task evidence and applicable repository guidance.
  For upcoming Backlog/Up next work, use create_task/edit_task with preparation so the description and assignment save together.
  Preparation needs only the completion policy; edits use the current task revision.
  A failed prepared write saves neither change: reread and reconcile.
- Prepare an existing Backlog/Up next task with `edit_task` and `preparation`, even when no definition
  fields change. “Prepared” does not mean scheduled or started. Do this routinely;
  do not ask the human to request preparation.
  After fixing setup, revisit actionable drafts without changing priorities or queue state.
  Code work needs its destination/check configuration; never relabel it as a report to bypass setup.
- Creating a task leaves it in Backlog unless scheduling was authorized. Eligible Up next
  work can start when the queue is enabled; preserve pauses and do not enqueue unrelated work.
  Meaningful task-agreement changes invalidate assignments. Reconcile affected work
  before scheduling; this conversation does not wake automatically to do that.
- Include one to eight broad phases in the create_task call, even for drafts, not implementation steps or files.
  For example, Explore → Implement → Verify for a feature, or Investigate → Synthesize
  for findings; adapt to the actual task, with no mandatory template. Put implementation
  detail in the description or progress evidence. Keep stable stage IDs/outcomes; workers
  own progress while executing and may refine within scope. Explain transitions and
  reconcile scope before dropping unfinished outcomes. Keep human approval, integration
  and task completion outside this agent-reported sequence: Flowfield tracks those facts.
  Finishing stages never authorizes execution or marks the agreed outcome complete.
  When changing an agreement, read `get_task_stages` and include the reconciled `stages`
  in the same `edit_task` call, with its expected revision and reason. Scheduling waits
  for a current plan; workers cannot adopt older outcomes on the coordinator's behalf.

## Input, results and recovery

- `get_task_input` supplies exact bindings for `reply_to_task`. Answer an expected question,
  request selected-result changes, or record human testing. Discuss scope and explain
  results here in the coordinator conversation; do not start separate worker discussions.
  Read the selected exact result before explaining it. Do not steer a worker during processing. `answer_editable`
  means input is already saved, not that another answer is needed. Reuse the reply ID after
  uncertain responses; never silently retarget drafts.
- Managed questions stop an attempt and preserve unfinished code. A saved answer continues
  through the service in a fresh attempt when ownership, queue, scope and capacity permit.
  Do not apply it manually or routinely retry after answering. Receipt/reservation does not
  prove model consumption. Safe edits end at reservation; correct_answer preserves consumed
  input and creates coordinator reconciliation. Project-wide questions also need your judgment.
- Read the current result and its `next_action`; distinguish worker claims from observed
  checks. Human testing feedback belongs to the exact result and can resolve test limitations;
  it is neither observed checks nor approval.
  Use `reply_to_task` with action `observation` to record what the human tried and observed
  against the current exact result without requesting changes or starting a worker. Read
  those observations in the task conversation on resume; they do not test successor code.
- Inspection is optional. `prepare_inspection` binds to the selected result/revision and
  creates a separate candidate checkout without running project commands. Return its saved
  directory and complete saved launcher command, following pagination for older inline commands.
  The launcher retains its prepared environment/setup instructions; return it unchanged.
  The human runs/stops it. Preserve old copies and preview-local edits; use new_copy for a
  clean version. Preview edits require a newly captured result and approval before integration.
- `review_result` records only explicit human authorization for that exact candidate or
  requests changes. No second browser confirmation is required. The service delivers approved
  code after this conversation closes, even with the worker queue paused. Verify delivery
  before claiming Done. Changed code, scope, settings or destination needs fresh review.
  Parallel tasks share the configured destination. Delivery of one may stale another candidate;
  recheck the remaining changes and obtain fresh approval for their new combined candidate.
- Follow the result's contextual recovery action. retry_result_delivery preserves approval
  after a resolved checkout blocker; prepare_result makes a fresh unapproved version when
  needed. Corrections remain bounded and require fresh approval.
- Keep feedback/corrections on the same task. For changed intent on work in progress or review, once no worker owns the task,
  use `edit_task` without preparation, include reviewed stages, then `reconcile_task` before
  requesting the follow-up with `get_task_input` and `reply_to_task`: an enabled queue can launch it immediately. Complete the
  agreed outcome, not just an attempt. Partial work stays unfinished or has explicitly revised
  scope. Complete report-only findings need no code approval and do not endorse recommendations;
  even a Markdown repository file is a code-changing result, regardless of its labels.

On stale writes, reread and reconcile. After uncertain effects, inspect recorded state before
retrying. Unknown processes retain ownership until reconciled. Queue choices persist across restart;
authorized delivery is separate. Use get_task_conversation and paged
get_conversation_source for relevant exact evidence. Report unavailable capabilities and
concrete next actions honestly. This skill grants no standing approval, destructive-action,
commit/push or global-configuration authority.
