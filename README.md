<h1 align="center">Flowfield</h1>

<p align="center"><strong>Ship software. Not slop.</strong></p>
<p align="center">A workspace for you and your coding agents.<br>Work through the idea, keep the context, and build across projects without losing the plot.</p>

<p align="center">
  <a href="https://flowfield.sh">Website</a> · <a href="https://docs.flowfield.sh/getting-started">Get started</a> · <a href="https://docs.flowfield.sh">Documentation</a>
</p>

<p align="center">
  <a href="https://pypi.org/project/flowfield-core/"><img src="https://img.shields.io/pypi/v/flowfield-core?logo=pypi&amp;logoColor=white&amp;color=2563eb" alt="PyPI version"></a>
  <a href="https://github.com/flowfield-sh/flowfield-core/actions/workflows/tests.yml"><img src="https://img.shields.io/github/actions/workflow/status/flowfield-sh/flowfield-core/tests.yml?branch=main&amp;label=tests" alt="Tests"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache_2.0-2563eb" alt="Apache 2.0 license"></a>
</p>

![The workspace board and Coordinator, with work spanning several projects](docs/images/board-hero.png)

## From conversation to finished work.

The idea is in one conversation. The implementation is in another. An agent needs an answer, a task is waiting on another task, and the reason you chose this approach is buried twenty messages back.

**Flowfield gives that work a home.** Delegate planning and coordination through one conversation while workers carry out focused tasks in your existing repositories. Shared knowledge stays in versioned documents, and questions and reviews come back to one Inbox. You decide what ships.

Flowfield works with **Codex, Claude Code and Pi**, with more harness support coming. Choose independently for the Coordinator and workers.

## Get started

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and sign in to a supported coding harness, then:

```sh
uv tool install flowfield-core
flowfield serve
```

Open [localhost:8765](http://127.0.0.1:8765), create a workspace and add your existing repositories. See [Getting started](https://docs.flowfield.sh/getting-started) for the walkthrough or [Installation](https://docs.flowfield.sh/installation) for requirements and alternatives.

## Keep the thinking with the work.

Ask the Coordinator to organize the work, maintain shared context and follow up on results. Use the board, documents and task controls yourself whenever you prefer. Conversation and direct actions work on the same plan.

**💬 Shape the idea**<br>Talk through the problem with the Coordinator. It turns agreed decisions into tasks and milestones while the work continues beside the conversation.

**🗂️ Connect your projects**<br>Work across your existing repositories in one workspace. The Coordinator can organize shared priorities and link dependent tasks across projects.

**📚 Give workers the right context**<br>The Coordinator maintains shared documents and selects the sources each task needs. Prepared tasks keep their exact document revisions, and newer revisions stay visible for review.

**⚡ Build in parallel**<br>Workers carry out independent tasks in separate Git checkouts using your tools and checks. Flowfield holds dependent work until its prerequisites are complete.

**📥 Know what needs you**<br>Inbox brings together questions, blockers and results. Ask the Coordinator to sort through them, or respond directly. Task feeds preserve progress, answers and evidence.

**🔎 Own the finish**<br>Ask the Coordinator to help review a result or request another pass. Inspect and try the code yourself when useful, then approve delivery through the conversation or task controls. Approval stays tied to the exact result.

## A closer look

<table>
  <tr>
    <td width="33%" align="center"><a href="docs/images/readme/board.png"><img src="docs/images/readme/board.png" alt="Workspace board with tasks across three projects"></a><br><strong>Board</strong></td>
    <td width="33%" align="center"><a href="docs/images/readme/documents.png"><img src="docs/images/readme/documents.png" alt="Shared workspace document with revision history"></a><br><strong>Documents</strong></td>
    <td width="33%" align="center"><a href="docs/images/readme/milestones.png"><img src="docs/images/readme/milestones.png" alt="Milestones grouping work across projects"></a><br><strong>Milestones</strong></td>
  </tr>
  <tr>
    <td align="center"><a href="docs/images/readme/inbox.png"><img src="docs/images/readme/inbox.png" alt="Inbox with a worker question, a blocker and code ready for review"></a><br><strong>Inbox</strong></td>
    <td align="center"><a href="docs/images/readme/task.png"><img src="docs/images/readme/task.png" alt="Task conversation with a worker question and reply controls"></a><br><strong>Task conversation</strong></td>
    <td align="center"><a href="docs/images/readme/review.png"><img src="docs/images/readme/review.png" alt="Code result with its diff, checks and review actions"></a><br><strong>Code review</strong></td>
  </tr>
</table>

[Explore the product →](https://flowfield.sh/product/)

## Go deeper

[Workspaces](https://docs.flowfield.sh/workspaces) · [Coordinator](https://docs.flowfield.sh/coordinator) · [Documents](https://docs.flowfield.sh/documents) · [Workers and review](https://docs.flowfield.sh/tasks) · [CLI](https://docs.flowfield.sh/cli)

Prefer planning from your own coding-agent session? [Connect a standalone agent](https://docs.flowfield.sh/agents/overview#standalone-conversations) through MCP.

## Contributing

Bug reports, feature requests and feedback are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for issue reporting and contribution policy. Report vulnerabilities through [private security reporting](https://github.com/flowfield-sh/flowfield-core/security/advisories/new).

### Build from source

Requires Python 3.12+, uv, Node 24+, pnpm 11.19.0 and Bun 1.3.11. Set `FLOWFIELD_BUILD_BUN` to the Bun executable before `make setup`.

```sh
make setup
pnpm --dir web build
uv run flowfield serve
```

Flowfield is licensed under the [Apache License 2.0](LICENSE).
