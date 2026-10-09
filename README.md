<h1 align="center">Flowfield</h1>

<p align="center"><strong>A workspace for you and your coding agents, with a board and task feeds.</strong></p>

<p align="center">
  <a href="https://pypi.org/project/flowfield-core/"><img src="https://img.shields.io/pypi/v/flowfield-core?logo=pypi&amp;logoColor=white&amp;color=2563eb" alt="PyPI version"></a>
  <a href="https://github.com/flowfield-sh/flowfield-core/actions/workflows/tests.yml"><img src="https://img.shields.io/github/actions/workflow/status/flowfield-sh/flowfield-core/tests.yml?branch=main&amp;label=tests" alt="Tests"></a>
  <a href="https://docs.flowfield.sh/getting-started"><img src="https://github.com/flowfield-sh/flowfield-core/actions/workflows/docs.yml/badge.svg?branch=main" alt="Documentation"></a>
</p>

![Flowfield board with priorities, questions and results ready for review](https://raw.githubusercontent.com/flowfield-sh/flowfield-core/main/docs/images/board-hero.png)

## What is Flowfield?

**💬 Plan with the Coordinator**\
Explore ideas, turn them into milestones and tasks, and refine the plan while workers build.
Open a card beside the conversation to discuss its scope or results.

**📋 Organize work on the board**\
Prioritize tasks, track dependencies, and see what’s queued, running or ready for review.
Needs you brings questions, blockers and review requests into one place.

**🧵 Follow each task in its feed**\
Keep definitions, progress, tool activity, questions, answers and results together.
See what changed and respond to the work that needs your attention.

**⚡ Run workers in parallel**\
Independent tasks run in separate Git checkouts with configurable worker capacity.
Dependencies hold work until it’s ready, and each worker uses your project’s tools and checks.

**🔎 Review and integrate results**\
Inspect code changes, try an exact result, and request revisions or approve delivery to your project.
Flowfield protects your working changes and keeps approval tied to the code you reviewed.

## Install

Install and sign in to [Codex CLI](https://developers.openai.com/codex/cli/), then:

```sh
uv tool install flowfield-core
flowfield harness install codex
flowfield serve
```

The browser opens at [localhost:8765](http://127.0.0.1:8765). See [Installation](https://docs.flowfield.sh/installation) for the pip alternative.

Choose **Add project** in the sidebar and select an existing directory, or register it from a terminal:

```sh
flowfield project init
```

Start planning in the [Coordinator](https://docs.flowfield.sh/coordinator), or connect a
[standalone coding agent](https://docs.flowfield.sh/integrations/codex#connect-a-standalone-coordinator).
Development builds use installed Codex through its native app-server and installed
[Claude Code](https://docs.flowfield.sh/integrations/claude-code) through the official
Agent SDK. Flowfield includes the SDK runtime; no extra integration install is needed.
Choose either harness independently for the Coordinator and workers. Published 0.2.1
predates this native integration update. Pi is the next planned integration.

## Fits your existing project

Choose an existing repository and use its tools, setup commands and tests. Keep architecture,
designs and coding conventions in your repository; Flowfield keeps ongoing work on the
board and execution evidence in task feeds.

Workers use separate Git checkouts. You review an exact code result before Flowfield
delivers it to your project’s configured branch. Your working changes stay protected.

## Documentation

[Getting Started](https://docs.flowfield.sh/getting-started) · [Concepts](https://docs.flowfield.sh/concepts) ·
[CLI](https://docs.flowfield.sh/cli) · [Integrations](https://docs.flowfield.sh/integrations/overview)

## Build from source

Requires Python 3.12+, uv, Node 24+, pnpm 11.19.0 and Bun 1.3.11.
Set `FLOWFIELD_BUILD_BUN` to the Bun executable before `make setup`.

```sh
make setup
pnpm --dir web build
uv run flowfield serve
```

See [CONTRIBUTING.md](https://github.com/flowfield-sh/flowfield-core/blob/main/CONTRIBUTING.md) for checks and issue reporting.
Report vulnerabilities through [private security reporting](https://github.com/flowfield-sh/flowfield-core/security/advisories/new).

## License

[Apache License 2.0](https://github.com/flowfield-sh/flowfield-core/blob/main/LICENSE).
