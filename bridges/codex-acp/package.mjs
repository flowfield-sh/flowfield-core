// Package the measured native Codex bridge through the shared bundle owner.
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { packageBridge } from "../package.mjs";

const root = dirname(fileURLToPath(import.meta.url));
const spec = JSON.parse(
  await readFile(
    join(root, "../../src/flowfield/adapters/codex_bridge.json"),
    "utf8",
  ),
);
await packageBridge({
  root,
  spec,
  name: "flowfield-codex-acp",
  label: "Flowfield Codex ACP bridge",
  upstream: "https://github.com/agentclientprotocol/codex-acp",
});
