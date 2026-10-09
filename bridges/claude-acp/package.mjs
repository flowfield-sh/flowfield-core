// Package a runtime build; never package the diagnostic/native-control proof entry.
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { packageBridge } from "../package.mjs";

const root = dirname(fileURLToPath(import.meta.url));
const spec = JSON.parse(
  await readFile(
    join(root, "../../src/flowfield/adapters/claude_bridge.json"),
    "utf8",
  ),
);
const built = JSON.parse(
  await readFile(join(root, ".work/upstream/package.json"), "utf8"),
);
if (built.version !== spec.version)
  throw new Error("Run build.mjs --runtime first");
await packageBridge({
  root,
  spec,
  name: "flowfield-claude-acp",
  label:
    "Flowfield Claude ACP bridge (SDK dependencies retain their own terms)",
  upstream: "https://github.com/agentclientprotocol/claude-agent-acp",
});
