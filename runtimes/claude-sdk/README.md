# Claude SDK runtime

Flowfield bundles the official Claude Agent SDK 0.3.293 in a standalone executable.
It runs the user's installed Claude Code via the SDK's public executable-path option.
Users do not install Node, Bun, an ACP adapter, or a separate Flowfield runtime.

Developers install dependencies with pnpm and set FLOWFIELD_BUILD_BUN to Bun 1.3.11.
Run node runtimes/claude-sdk/build.mjs --host for local checks, or make build for all
Apple Silicon and Linux arm64/x64 binaries and distributions. Generated binaries are ignored.
Platform wheels contain one runtime; the source archive contains all three compressed with XZ.
The Python build hook expands only the selected platform; no JavaScript tools are needed.

runtime.mjs translates public SDK messages and permissions. cleanup.mjs interrupts
the foreground turn, stops tracked native tasks, observes two fresh quiet snapshots,
and verifies its exact owned Claude process has exited. Unsupported or refused cleanup
remains unconfirmed. SDK and Claude internals are not patched. Prompt contents, thinking,
credentials and private native payloads do not enter diagnostics.

node --test runtimes/claude-sdk/cleanup.test.mjs checks lifecycle logic; Python's
tests/test_claude_agent.py exercises the compiled runtime and official SDK against a
scripted native CLI, including independently owned background processes. These checks
make no live model calls. Real inference and provider access require a separate live trial.
