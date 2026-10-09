# Native adapters

The application owns scheduling, permissions, context, session generations and exact
code approval. `Agent` is the application contract. Codex uses its installed app-server
directly; Claude uses the official Agent SDK through the helper included in Flowfield.

Native transport, model catalogs, configuration and public event translation stay in
these adapters. Accounts and native settings remain harness-owned. Both integrations
stop active turns and supported tracked background work. An uncertain shutdown retains
ownership; process-group exit alone is insufficient. LocalProcess owns exact live handles
and never reconstructs ownership from saved PIDs. No automatic prompt replay.
