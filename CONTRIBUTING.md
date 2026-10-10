# Issues and development

Bug reports, feedback and feature requests are welcome in [GitHub Issues](https://github.com/flowfield-sh/flowfield-core/issues).
We are not accepting external pull requests at this time.

For a bug, include the Flowfield version, operating system, steps to reproduce, expected
behavior and actual behavior. Remove secrets and private project content from logs or screenshots.
Report potential vulnerabilities using [private security reporting](SECURITY.md).

## Follow development

Maintainers track public work in issues: one issue per user-facing outcome, described
in plain prose with clear completion criteria.

Updates describe meaningful progress or a blocker, linking relevant code or a released
version when useful. Close an issue when its described outcome is available, or explain
why the work was cancelled. Bug reports and feature requests can link the relevant issue.

A work issue can be as simple as:

```md
Let people search their project's work history and follow results back to the original
task conversation.

This is complete when search supports useful filters, links to exact sources, and clearly
distinguishes current work from superseded text and archived tasks.
```

## Develop locally

Python 3.12+, uv, Node 24+, pnpm 11.19.0 and Bun 1.3.11 are required.
Set `FLOWFIELD_BUILD_BUN` to the Bun executable before setup.

```sh
make setup
make check
pnpm --dir web exec playwright install chromium
make smoke
make build
```

`make check` runs Python formatting, lint, types and deterministic tests, generated API
type validation, native cleanup tests, frontend checks/build, and Mintlify validation/link checks. `make smoke`
runs Chromium against the built application. `make build` produces three platform wheels
and a portable source archive with the compiled UI and SDK helper. Both install without Node, pnpm or a checkout at runtime.
`scripts/check_dist.py` copies the install check and scripted native peer into each disposable
environment to exercise the installed package.
`make check-dist` installs both distributions through pip and the wheel through isolated
`uv tool install`, then exercises each installed application outside the checkout.

For frontend development, run `uv run flowfield serve` and `pnpm --dir web dev` in separate
terminals. API wire types are generated with `make api-types`. Application operations live
under `src/flowfield/`; harness-specific behavior belongs in `src/flowfield/adapters/`.

Ordinary checks use isolated disposable state and never launch live model calls.

Browser journeys live in focused `web/e2e/*.spec.ts` suites and run with two workers
using the `chromium` channel (full Chromium in headless mode). Keep the browser binary
paired with the locked Playwright version. Native-tab behavior must use actual clicks
and page events; a direct `newPage().goto()` does not test link activation.
Each journey owns its project IDs, task prefixes and directories; tests must not depend
on another test's records or order. `support.ts` shares CLI/MCP helpers and model-free
catalog interception. Test the actual app rather than a separate mock interface.
Model catalog interception belongs to the browser context so newly opened tabs inherit
it before their first request. Notification journeys run afterward because they change
service-wide preferences and clear shared notices. Scope alert assertions to the relevant
form or notification.
After building the UI, run a focused suite with
`pnpm --dir web exec playwright test e2e/navigation.spec.ts`.
The native-tab regression allocates separate project IDs and prefixes for repetitions:
`pnpm --dir web exec playwright test e2e/native-tabs.spec.ts --repeat-each=20`.
CI also repeats it ten times, with no retries; every repetition must pass. Other suites
still use fixed fixture IDs, so repeat those through separate Playwright invocations.
Use `uv run pytest --durations=25` to profile backend checks before optimizing them;
keep real Git, cleanup, concurrency and exact-approval coverage intact.

## CLI output

Every data command must have a readable default reply: lead with its outcome or status,
label details, state empty results explicitly, and show a concrete next step when blocked.
Never dump JSON, Python dictionaries or lists as the default presentation. Preserve literal
user-authored text and deliberate text exports. Reserve machine-readable output for an
explicit `--json`; it must remain parseable without human prose, with errors on stderr and
a nonzero exit status. Keep pagination and incomplete-data notices visible in human replies.
When adding or changing a command, verify its default and JSON paths, including meaningful
empty and error cases, against actual response shapes and mutation receipts.

## Database changes

Schema 44 is the initialization baseline, captured in
`tests/fixtures/schema_44.sql`. Append each schema change to the ordered registry in
`src/flowfield/migrations.py`; do not edit previous migrations or the baseline SQL.
Databases from 0.1.0 and earlier development builds are unsupported. New workspaces
start at this baseline; startup never resets an older workspace.
Migration callbacks change only the database using `execute`/`executemany`. The storage
owner controls the transaction, version, migration history and pre-upgrade snapshot;
callbacks must not commit, use `executescript`, or mutate project files or artifacts.

Test fresh initialization and upgrading populated fixtures, including failure rollback,
interruption, restart and preserved application bindings. Keep application writes inside
`Workspace.connection()` so recovery can detect newer work. Automatic snapshots cover
the database; full workspace backups remain separate.

## Documentation

Mintlify content and configuration live in `docs/`. Its CLI is pinned and locked locally:

```sh
make docs
make docs-serve
```

`make docs` validates the build and internal links. `make docs-serve` starts a local
preview. Hosted deployment uses the `docs/` subdirectory and its `docs.json` configuration.
Keep user documentation concise and accurate for the behavior being released.
Mintlify builds `docs/` from the `docs` branch. Advance that branch to the reviewed release
commit after publication, using a normal fast-forward push, then verify the hosted pages.
Feature development continues on main.

Native runtime builds use Bun 1.3.11, Node and pnpm during development only. Set
`FLOWFIELD_BUILD_BUN` to the Bun executable before `make setup` or `make build`.
The runtime uses pinned SDK 0.3.293 and ships inside macOS 13+ Apple Silicon and glibc 2.28+ Linux
arm64/x64 wheels. x64 builds use Bun's baseline CPU target. Windows is not supported.

## Versioning and releases

`pyproject.toml` owns the version; uv keeps its entry in
`uv.lock` aligned. Tags use `vX.Y.Z`. During 0.x, patches contain compatible fixes;
minor releases contain new capabilities or breaking changes, with explicit upgrade notes.
From 1.0 onward, Semantic Versioning applies: major for breaking changes, minor for
compatible features, patch for compatible fixes. Compatibility covers documented CLI/MCP
interfaces, integration guidance and persisted workspace state. Schema changes still need
tested migrations and recovery; a version bump does not permit discarding user state.

Every push to main runs the `Tests` workflow: Python 3.12 tests on Linux/macOS, Python and
frontend quality checks, generated API validation, Mintlify validation, Chromium journeys
and clean installed-package checks. The bundled Claude SDK runtime is built and checked against scripted native peers
on Apple Silicon and Linux arm64/x64 without models; see [runtime builds](runtimes/claude-sdk/README.md).
Scripted checks do not establish real native compatibility on each platform.
Live model calls are separate from CI.

### Prepare and rehearse

Review all commits and the aggregate diff since the last published release, then write a nonempty `## X.Y.Z` section in `CHANGELOG.md`, newest first. Verify each claim against implementation, documentation and checks. Preserve previous entries and commit reviewed notes before releasing.

Group release notes under `### Features` and `### Bug fixes`, omitting empty sections. Use `### Improvements` for meaningful refinements to existing behavior. Add `### Breaking changes`, `### Security` or `### Upgrade` only when compatibility, security or required user action warrants it. Put a change in one section rather than repeating it.

Curate the changes users need to know: what they can do now, what works better and what they must do when upgrading. Combine related changes, keep bullets short and omit minor polish, internal refactors, test results, implementation details and exhaustive commit inventories. Documentation changes belong only when they materially change how users use Flowfield. Link to detailed guides for recovery or setup. Do not impose an item count or line-length limit. Write each paragraph and bullet on one source line; use blank lines for Markdown structure, without manual wrapping or forced line breaks.

```sh
make setup
pnpm --dir web exec playwright install chromium
make release-check
uv run --no-sync python scripts/release.py 0.2.0 --dry-run
```

`release-check` runs local checks, browser journeys, clean builds, pip wheel/source and uv tool
installation checks, and distribution metadata validation. Build clears the generated `dist/`
directory. The preview checks branch, clean state, remotes, version, notes and unused tags,
and prints outgoing commits; it does not run checks or change files/refs.

The `Release` workflow in `.github/workflows/workflow.yml` also supports a manual run on
main. It runs CI, builds distributions once, and verifies those same artifacts on Linux/macOS.
Manual runs stop after verification. Artifacts and checksums remain available for 30 days.
Platform wheels include the Claude SDK runtime. The source archive includes all three
prebuilt, compressed runtimes so installing it needs no JavaScript tools.

### Publish

Configure the PyPI Trusted Publisher for owner `flowfield-sh`, repository `flowfield-core`,
workflow `workflow.yml` and environment `pypi`. The matching GitHub environment permits only
tags matching `v*`. Publishing uses short-lived GitHub identity tokens.

After reviewing the preparation and explicitly authorizing publication, run from clean main:

```sh
uv run --no-sync python scripts/release.py 0.2.0
```

The command holds a repository release lock, checks identity/notes, bumps only the version
files when needed, runs verification, and commits those version files. It rechecks the
remote and atomically pushes main and the annotated tag, then watches the release run.
CI requires the tag to match package/lock metadata and identify a commit on main. Only
the tag-triggered publishing job uses the `pypi` environment and OIDC permission. It publishes
verified original artifacts, then publishes the GitHub Release with reviewed notes and checksums.
Check actual PyPI installation, update discovery and GitHub assets after publication.

### Recover

Failures preserve local state and completed steps. Never move a published tag or rebuild an
uploaded version. Before tagging, correct and review retained changes, then retry. Exit 75
means another local release owns the lock. If push fails, inspect local and remote refs before
retrying the same atomic push. If CI fails, inspect the existing run; source corrections need
a new commit/version/tag. For partial upload or GitHub failure, rerun only failed jobs to reuse
the original artifacts. Do not rerun the build after uploading. uv skips already uploaded
identical files; GitHub completes the existing draft/assets. Expired artifacts require recovering
and verifying the exact originals. A bad published release needs a new version.
