<h1 align="center">Flowfield</h1>

<p align="center"><strong>Ship software. Not slop.</strong></p>
<p align="center">A workspace for you and your coding agents.<br>Shape the plan, build across projects, and bring every outcome through review.</p>

<p align="center">
  <a href="https://flowfield.sh">Website</a> · <a href="https://docs.flowfield.sh/getting-started">Get started</a> · <a href="https://docs.flowfield.sh">Documentation</a>
</p>

<p align="center">
  <a href="https://pypi.org/project/flowfield-core/"><img src="https://img.shields.io/pypi/v/flowfield-core?logo=pypi&amp;logoColor=white&amp;color=2563eb" alt="PyPI version"></a>
  <a href="https://github.com/flowfield-sh/flowfield-core/actions/workflows/tests.yml"><img src="https://img.shields.io/github/actions/workflow/status/flowfield-sh/flowfield-core/tests.yml?branch=main&amp;label=tests" alt="Tests"></a>
  <a href="https://github.com/flowfield-sh/flowfield-core/actions/workflows/docs.yml"><img src="https://img.shields.io/github/actions/workflow/status/flowfield-sh/flowfield-core/docs.yml?branch=main&amp;label=docs" alt="Documentation"></a>
</p>

![The Coordinator beside task priorities, review and delivered outcomes](docs/images/board-hero.png)

The idea is in one conversation. The implementation is in another. An agent needs an answer, a task is waiting on another task, and the reason you chose this approach is buried twenty messages back.

**Flowfield gives that work a home.** Plan with the Coordinator while workers carry out focused tasks across your existing repositories. Keep shared knowledge in versioned documents, let a Reviewer assess each complete outcome, and answer questions in one Inbox. Code, reports and files stay connected to the work that produced them. You decide which code ships.

Flowfield runs locally and works with **Codex, Claude Code and Pi**. Choose independently for the Coordinator, Workers and Reviewer.

## Get started

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and sign in to a supported coding harness, then:

```sh
uv tool install flowfield-core
flowfield serve
```

Open [localhost:8765](http://127.0.0.1:8765), create a workspace and choose its agents. Add existing repositories for code work, or start with a report or file task. See [Getting started](https://docs.flowfield.sh/getting-started) for the walkthrough or [Installation](https://docs.flowfield.sh/installation) for requirements and alternatives.

## How does Flowfield work?

Ask the Coordinator to organize work and maintain shared context. Each task owns a useful outcome, its history and delivery. Order Backlog and Up next on Task Board, edit documents and respond through Inbox while the same conversation stays beside the work.

**💬 Shape the idea**<br>Talk through the problem with the Coordinator. It turns agreed decisions into tasks while the work continues beside the conversation.

**🗂️ Connect your projects**<br>Bring your API, application and documentation into one workspace. Link dependent tasks across projects, or create reports and files without a repository.

**📚 Give workers the right context**<br>The Coordinator maintains shared documents and selects the sources each task needs. Prepared tasks keep their exact document revisions, and newer revisions stay visible for review.

**⚡ Build in parallel**<br>Workers carry out independent tasks in separate checkouts using your tools and checks. Larger tasks can use local subtasks that assemble into one result. When another delivery changes the destination, Flowfield prepares the remaining work against it and queues corrections when needed. Dependent work waits for delivered prerequisites.

**📥 Know what needs you**<br>Inbox separates decisions in Needs me, completed outcomes in Delivered and earlier responses in History. Answer a question once and managed work continues when eligible. Task feeds preserve the story.

**🔎 Own the finish**<br>The Reviewer assesses the whole task and can request corrections. Inspect or run the result, then approve exact code for delivery to your local branch. Complete reports and files finish after configured review and remain available in Files. Ask the Coordinator to run, push or publish a result when you want those actions.

## Screenshots

<table>
  <tr>
    <td width="33%" align="center"><a href="docs/images/readme/board.png"><img src="docs/images/readme/board.png" alt="Workspace board with code, file and report tasks"></a><br><strong>Tasks</strong></td>
    <td width="33%" align="center"><a href="docs/images/readme/documents.png"><img src="docs/images/readme/documents.png" alt="Shared workspace document with revision history"></a><br><strong>Documents</strong></td>
    <td width="33%" align="center"><a href="docs/images/readme/files.png"><img src="docs/images/readme/files.png" alt="Retained task files with folders and a file inspector"></a><br><strong>Files</strong></td>
  </tr>
  <tr>
    <td align="center"><a href="docs/images/readme/inbox.png"><img src="docs/images/readme/inbox.png" alt="Inbox with a question and code ready for review"></a><br><strong>Inbox</strong></td>
    <td align="center"><a href="docs/images/readme/task.png"><img src="docs/images/readme/task.png" alt="Task history with local subtasks, work and whole-task review"></a><br><strong>Task history</strong></td>
    <td align="center"><a href="docs/images/readme/review.png"><img src="docs/images/readme/review.png" alt="Code result with its diff, checks and review actions"></a><br><strong>Code review</strong></td>
  </tr>
</table>

## Contributing

Bug reports, feature requests and feedback are welcome. See [CONTRIBUTING.md](https://github.com/flowfield-sh/flowfield-core/blob/main/CONTRIBUTING.md) for issue reporting and contribution policy. Report vulnerabilities through [private security reporting](https://github.com/flowfield-sh/flowfield-core/security/advisories/new).

### Build from source

Requires Python 3.12+, uv, Node 24+, pnpm 11.19.0 and Bun 1.3.11. Set `FLOWFIELD_BUILD_BUN` to the Bun executable before `make setup`.

```sh
make setup
pnpm --dir web build
uv run flowfield serve
```

Flowfield is licensed under the [Apache License 2.0](https://github.com/flowfield-sh/flowfield-core/blob/main/LICENSE).
