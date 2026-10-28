# Contributing

Bug reports, feature requests and feedback are welcome in [GitHub Issues](https://github.com/flowfield-sh/flowfield-core/issues).

We are not accepting external pull requests or code contributions at this time. Please open an issue instead.

For bugs, include the Flowfield version, operating system, steps to reproduce, and expected and actual behavior. Remove secrets and private project content from logs or screenshots.

Report security vulnerabilities [privately](SECURITY.md).

After checkout setup, run `uv run python scripts/usage-preview.py` for a populated, isolated Usage preview. It builds the current frontend and listens on port 8780, then prints the actual fixed-period workspace and task URLs, its temporary state/log directory, and stop instructions. The fixed synthetic period contains 4,800 known workspace tokens, including a 3,800-token task with persisted Claude Code, Codex and Pi examples, archived work, partial/unknown coverage and unassigned Coordinator activity. Queues stay paused, update checks are disabled in the disposable store and no model is called. An occupied port fails safely; the launcher never stops another service. Stop with Ctrl-C (or the printed PID command); confirmed shutdown removes the owned state. Startup or unconfirmed shutdown failures retain diagnostics. Rerun the command for a fresh preview; no companion service is needed.
