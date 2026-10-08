// Signed-out, model-free SDK control-plane measurement. No user message is sent.
import { query } from "@anthropic-ai/claude-agent-sdk";
import { spawn } from "node:child_process";
import { mkdtemp, mkdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

const root = await mkdtemp(join(tmpdir(), "flowfield-claude-control-"));
const home = join(root, "home");
await mkdir(home);
let endInput;
const input = { async *[Symbol.asyncIterator]() {
  await new Promise((resolve) => { endInput = resolve; });
} };
let child, exited;
let exitObserved = false;
let forcedExit = false;
const snapshots = [];
const report = { noPromptSent: true, isolatedNativeConfig: true };
let q;
async function bounded(promise, ms = 15000) {
  let timer;
  try {
    return await Promise.race([promise, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error("Proof deadline exceeded")), ms);
    })]);
  } finally { clearTimeout(timer); }
}
try {
  q = query({ prompt: input, options: {
    pathToClaudeCodeExecutable: process.env.CLAUDE_CODE_EXECUTABLE,
    cwd: root,
    env: { HOME: home, CLAUDE_CONFIG_DIR: join(home, ".claude"), PATH: "",
      CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC: "1", DISABLE_AUTOUPDATER: "1" },
    settingSources: [], persistSession: false,
    systemPrompt: { type: "preset", preset: "claude_code" },
    spawnClaudeCodeProcess(options) {
      if (child) throw new Error("Unexpected native owner replacement");
      child = spawn(options.command, options.args, {
        cwd: options.cwd, env: options.env, signal: options.signal,
        stdio: ["pipe", "pipe", "pipe"],
      });
      child.stderr.resume(); // Never store native diagnostics or credentials.
      exited = new Promise((resolve) => {
        child.once("exit", (code, signal) => { exitObserved = true; resolve({ code, signal }); });
        child.once("error", () => resolve({ spawnFailed: true }));
      });
      return child;
    },
  } });
  const drained = (async () => {
    for await (const message of q) {
      if (message.type === "system" && message.subtype === "background_tasks_changed") {
        snapshots.push(message.tasks.length);
      }
    }
  })();
  // Attach a handler immediately; close can reject the iterator.
  const drainOutcome = drained.catch(() => undefined);
  report.stage = "initialize";
  const init = await bounded(q.initializationResult());
  report.models = init.models.map((m) => ({ id: m.value, resolvedModel: m.resolvedModel,
    supportsEffort: m.supportsEffort, effortLevels: m.supportedEffortLevels }));
  report.commands = init.commands.map((c) => c.name);
  report.auth = { apiProvider: init.account?.apiProvider, apiKeySource: init.account?.apiKeySource,
    tokenSource: init.account?.tokenSource, hasSubscription: Boolean(init.account?.subscriptionType) };
  report.stage = "background-snapshot";
  await bounded(q.reinitialize());
  // The snapshot follows the control response on the stream. Bound the wait.
  const snapshotDeadline = Date.now() + 3000;
  while (!snapshots.length && Date.now() < snapshotDeadline) {
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  if (!snapshots.length) throw new Error("Missing native snapshot");
  report.backgroundSnapshotSizes = snapshots;
  report.stage = "interrupt";
  report.interruptReceipt = await bounded(q.interrupt());
  report.stage = "context-summary";
  report.context = await bounded(q.getContextUsage({ detail: "summary" }));
  // Context contains filenames and other details in some releases. Keep only these fields.
  report.context = { model: report.context.model, rawMaxTokens: report.context.rawMaxTokens };
  report.stage = "close";
  endInput?.();
  q.close();
  await bounded(exited, 10000);
  await bounded(drainOutcome, 3000);
  report.stage = "complete";
} catch (error) {
  // Do not print native errors: they can contain host/account data.
  report.failed = true;
  report.errorType = error?.name;
  process.exitCode = 1;
} finally {
  endInput?.();
  q?.close();
  if (child && !exitObserved) {
    try { await bounded(exited, 5000); }
    catch {
      forcedExit = true;
      child.kill("SIGKILL"); // Only the exact child created by this proof.
      await bounded(exited, 3000);
    }
  }
  report.nativeOwnerExitObserved = exitObserved;
  report.forcedExit = forcedExit;
  // No tool ran, so this establishes no detached-tool cleanup guarantee.
  report.nativeWorkCleanupVerified = false;
  await rm(root, { recursive: true, force: true });
  console.log(JSON.stringify(report));
}
