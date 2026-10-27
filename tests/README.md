# Test coverage by platform

The full Python suite runs on Linux x64. Every test remains in that gate; no production behavior is excluded from CI. Browser journeys run once across two disjoint Linux shards, with focused Firefox recovery coverage. Original wheel, source and uv-tool installs run on Linux x64, Linux ARM64 and Apple Silicon.

Additional platforms repeat tests whose evidence depends on the host, using pytest's native argument files:

- `pytest @tests/native-platform.txt` exercises real Codex/Pi subprocess transports, the compiled Claude runtime, native shutdown, cancellation races, child process groups, host tools, independent ports and borrowed-service preservation. Linux ARM64 runs this selection; macOS includes it too. Pure protocol validation, labels, model choices and error classification stay in the full Linux suite.
- `pytest @tests/macos-platform.txt` adds Git checkout delivery and worktrees, executable modes and literal paths, SQLite ownership/upgrade/recovery, symlink-safe file capture/deletion, native service restart, shell quoting and inspection launchers, parallel execution and exact delivery. Pure task/document/review state machines, projections, API schemas and formatting are not repeated merely because their fixtures create temporary repositories.

Keep these selections tied to actual platform boundaries when adding or changing tests. A renamed or missing node fails collection; do not silently filter unavailable tests. Whole modules are selected only when every case exercises the retained boundary. Run either selection locally with `--collect-only -q` to inspect the exact cases, including parameter variants.

The JavaScript cleanup state-machine tests use a simulated child process and run once on Linux. Actual compiled runtime/process cleanup remains covered on every supported platform. Browser fixture lifecycle checks run once, before shard 1, rather than twice on identical Linux hosts. Documentation validation runs in the quality/build job, without a second identical workflow. A failing shard or quality check still fails the aggregate gate.

Tests use isolated state and scripted peers, never live model calls. Platform selection does not replace artifact installation, source freshness or exact approval/delivery checks.
