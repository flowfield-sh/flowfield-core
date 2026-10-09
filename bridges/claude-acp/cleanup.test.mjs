import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";
import { Cleanup, capability, parseQuiesce } from "./cleanup.mjs";

async function fixture(options = {}) {
  const child = new EventEmitter();
  let tasks = options.tasks ?? [];
  let current;
  let snapshotCalls = 0;
  const stopped = [];
  const query = {
    interrupt: async () => options.interrupt ?? { still_queued: [] },
    reinitialize: async () => {
      snapshotCalls++;
      if (options.snapshot) await options.snapshot({ cleanup, query, child, snapshotCalls });
      else cleanup.observe("root", query, { type: "system", subtype: "background_tasks_changed", tasks });
    },
    stopTask: async (id) => {
      stopped.push(id);
      if (options.stopTask) return options.stopTask(id);
      tasks = tasks.filter((task) => task.task_id !== id);
    },
  };
  const cleanup = new Cleanup(() => current, {
    timeoutMs: options.timeoutMs ?? 300,
    spawnProcess: () => child,
    ...options.controller,
  });
  current = {
    query,
    startConsumer() {},
    cancel: options.cancel ?? (async () => {}),
    close: async () => {
      if (options.close) await options.close({ cleanup, query, child });
      else child.emit("exit", 0);
    },
  };
  await cleanup.attach(async () => {
    cleanup.spawn("root", { command: "not-executed", args: [] });
    return { sessionId: "root" };
  });
  return { cleanup, query, child, stopped, setQuery(value) { current.query = value; } };
}

test("confirmed cleanup requires two fresh empty observations and exact native exit", async () => {
  const { cleanup } = await fixture();
  const receipt = await cleanup.stop("root");
  assert.deepEqual(receipt, { ...capability, sessionId: "root", status: "confirmed", reason: null,
    checkedNativeOwners: 1, stoppedTasks: 0, quietObservations: 2, nativeOwnerExited: true });
  assert.equal(await cleanup.stop("root"), receipt);
  assert.throws(() => cleanup.run(() => {}), /stopped/);
  assert.throws(() => cleanup.stop("wrong"), /Unknown/);
});

test("native task acknowledgments do not suffice: membership must become empty", async () => {
  const { cleanup, stopped } = await fixture({ tasks: [
    { task_id: "bash", task_type: "local_bash", description: "private command" },
    { task_id: "agent", task_type: "local_agent" },
  ] });
  const receipt = await cleanup.stop("root");
  assert.equal(receipt.status, "confirmed");
  assert.deepEqual(stopped, ["bash", "agent"]);
  assert.equal(receipt.stoppedTasks, 2);
  assert.equal(JSON.stringify(receipt).includes("private command"), false);
});

test("a task that remains live is uncertain and the fence remains sealed", async () => {
  const { cleanup } = await fixture({ tasks: [{ task_id: "task", task_type: "local_bash" }], stopTask: async () => {} });
  const receipt = await cleanup.stop("root");
  assert.equal(receipt.status, "uncertain");
  assert.equal(receipt.reason, "timeout");
  assert.throws(() => cleanup.run(() => {}), /stopped/);
});

test("termination attempts are bounded even when the same task keeps acknowledging Stop", async () => {
  const { cleanup, stopped } = await fixture({ tasks: [{ task_id: "task", task_type: "local_bash" }],
    stopTask: async () => {}, controller: { maxTasks: 2 } });
  assert.equal((await cleanup.stop("root")).reason, "resource_limit");
  assert.equal(stopped.length, 2);
});

test("a missing fresh snapshot cannot reuse a previously empty set", async () => {
  const { cleanup, query } = await fixture({ snapshot: async () => {} });
  cleanup.observe("root", query, { type: "system", subtype: "background_tasks_changed", tasks: [] });
  const receipt = await cleanup.stop("root");
  assert.equal(receipt.status, "uncertain");
  assert.equal(receipt.reason, "timeout");
});

test("unknown and malformed task membership cannot produce a receipt", async () => {
  for (const tasks of [null, [{ task_id: "x", task_type: "remote_unknown" }],
    [{ task_id: "x", task_type: "local_agent" }, { task_id: "x", task_type: "local_agent" }]]) {
    const { cleanup } = await fixture({ snapshot: async ({ cleanup, query }) => {
      cleanup.observe("root", query, { type: "system", subtype: "background_tasks_changed", tasks });
    } });
    const receipt = await cleanup.stop("root");
    assert.equal(receipt.status, "uncertain");
    assert.equal(receipt.reason, "invalid_native_state");
  }
});

test("unexpected native exit is not native task cleanup", async () => {
  const { cleanup, child } = await fixture();
  child.emit("exit", 0);
  const receipt = await cleanup.stop("root");
  assert.equal(receipt.reason, "native_owner_exited_early");
  assert.equal(receipt.nativeOwnerExited, false);
});

test("query replacement and old-owner events cannot be reconciled against a new owner", async () => {
  const { cleanup, setQuery } = await fixture();
  setQuery({});
  assert.equal((await cleanup.stop("root")).reason, "native_owner_changed");
});

test("queued work and absent interrupt receipts stay uncertain", async () => {
  for (const interrupt of [{}, { still_queued: ["remaining"] }]) {
    const { cleanup } = await fixture({ interrupt });
    assert.equal((await cleanup.stop("root")).reason, "queued_work_unknown");
  }
});

test("cleanup waits for accepted work to settle after cancel", async () => {
  let release;
  const { cleanup } = await fixture({ cancel: async () => release() });
  const accepted = cleanup.run(() => new Promise((resolve) => { release = resolve; }));
  await Promise.resolve();
  const stopped = cleanup.stop("root");
  assert.throws(() => cleanup.run(() => {}), /stopped/);
  await accepted;
  assert.equal((await stopped).status, "confirmed");
});

test("active goals and in-flight native hooks stay uncertain", async () => {
  for (const message of [
    { type: "active_goal", value: { condition: "do work" } },
    { type: "hook_started", hook_id: "hook" },
  ]) {
    const { cleanup, query } = await fixture();
    cleanup.observe("root", query, message);
    assert.equal((await cleanup.stop("root")).status, "uncertain");
  }
});

test("late activity during native close invalidates otherwise quiet observations", async () => {
  const { cleanup } = await fixture({ close: async ({ cleanup, query, child }) => {
    cleanup.observe("root", query, { type: "task_started" });
    child.emit("exit", 0);
  } });
  const receipt = await cleanup.stop("root");
  assert.equal(receipt.reason, "native_work_after_quiet");
  assert.equal(receipt.status, "uncertain");
});

test("without observed native exit, quiet membership is insufficient", async () => {
  const { cleanup } = await fixture({ close: async () => {} });
  assert.equal((await cleanup.stop("root")).reason, "timeout");
});

test("a second managed root and new native startup after Stop are rejected", async () => {
  const { cleanup } = await fixture();
  await assert.rejects(cleanup.attach(async () => ({ sessionId: "other" })), /one root/);
  await cleanup.stop("root");
  assert.throws(() => cleanup.spawn("root", { command: "not-executed", args: [] }), /forbidden/);
});

test("quiesce input has only a bounded exact session identity", () => {
  assert.deepEqual(parseQuiesce({ sessionId: "root" }), { sessionId: "root" });
  for (const value of [null, [], {}, { sessionId: "" }, { sessionId: "x".repeat(501) },
    { sessionId: "root", replaceOwner: true }]) assert.throws(() => parseQuiesce(value));
});
