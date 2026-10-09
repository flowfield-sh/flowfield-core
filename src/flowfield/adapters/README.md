# Native adapters

The application owns scheduling, permissions, context, session generations and exact
code approval. `Agent` is the application contract. Codex uses its installed app-server
directly; Claude uses the official Agent SDK through the helper included in Flowfield;
Pi uses its installed JSONL RPC interface and an automatically loaded scoped MCP extension.

Native transport, model catalogs, configuration and public event translation stay in
these adapters. Accounts and native settings remain harness-owned. Native integrations
stop active turns and supported tracked background work. An uncertain shutdown retains
ownership; process-group exit alone is insufficient. LocalProcess owns exact live handles
and never reconstructs ownership from saved PIDs. No automatic prompt replay.

Pi reuses the bounded transport/process owner through a wire-format codec. Provider/model
identity is escaped unambiguously; thinking levels and context headroom come from native
reports. Managed Pi disables custom extensions and trust-gated project configuration;
standard tools have no separate native background-task API. Stop clears queued work before
native abort, checks idle state and requires graceful session shutdown plus owned exit.
