// Candidate native cleanup, enabled explicitly in the development proof only.
// Public SDK controls; no private RPC, process scanning or history mutation.
import { spawn } from "node:child_process";

export const capability = Object.freeze({
  version: 1, method: "_flowfield/quiesce", scope: "native-turns-and-tasks",
});
// Monitor/workflow termination has not been measured; fail closed for those tasks.
const taskTypes = new Set(["local_bash", "local_agent"]);
const idIsValid = (id) => typeof id === "string" && id.length > 0 && id.length <= 500;

export function parseQuiesce(value) {
  if (!value || typeof value !== "object" || Array.isArray(value) ||
      Object.keys(value).length !== 1 || !idIsValid(value.sessionId)) {
    throw new Error("Expected only a bounded sessionId");
  }
  return { sessionId: value.sessionId };
}

export class Cleanup {
  constructor(state, { enabled = true, timeoutMs = 10000, maxTasks = 256, spawnProcess = spawn } = {}) {
    if (typeof enabled !== "boolean" || !Number.isFinite(timeoutMs) || timeoutMs <= 0 || timeoutMs > 10000 ||
        !Number.isInteger(maxTasks) || maxTasks < 1 || maxTasks > 256) throw new Error("Invalid cleanup bounds");
    this.state = state;
    this.enabled = enabled;
    this.timeoutMs = timeoutMs;
    this.maxTasks = maxTasks;
    this.spawnProcess = spawnProcess;
    this.pending = new Set();
    this.sessionId = null;
    this.claimed = false;
    this.sealed = false;
    this.result = null;
    this.owner = null;
    this.query = null;
    this.replaced = false;
    this.invalid = false;
    this.tasks = null;
    this.snapshot = 0;
    this.epoch = 0;
    this.goalActive = false;
    this.hooks = new Set();
  }

  run(operation) {
    if (!this.enabled) return operation();
    if (this.sealed) throw new Error("This managed bridge has been stopped");
    const promise = Promise.resolve().then(operation);
    this.pending.add(promise);
    promise.then(() => this.pending.delete(promise), () => this.pending.delete(promise));
    return promise;
  }

  attach(operation, existingId = null) {
    if (!this.enabled) return operation();
    return this.run(async () => {
      if (this.claimed) throw new Error("Managed bridges own one root session");
      this.claimed = true;
      const result = await operation();
      const id = existingId ?? result?.sessionId;
      if (!idIsValid(id) || this.owner?.sessionId !== id) throw new Error("Missing native owner");
      const state = this.state(id);
      if (!state?.query) throw new Error("Missing native query");
      this.sessionId = id;
      this.query = state.query;
      return result;
    });
  }

  spawn(sessionId, options) {
    if (this.sealed) throw new Error("Native startup after Stop is forbidden");
    if (this.owner) this.replaced = true;
    const child = this.spawnProcess(options.command, options.args, {
      cwd: options.cwd, env: options.env, signal: options.signal, stdio: ["pipe", "pipe", "pipe"],
    });
    const owner = { sessionId, child, exited: false, failed: false };
    owner.exit = new Promise((resolve) => {
      child.once("exit", () => { owner.exited = true; this.epoch++; resolve(); });
      child.once("error", () => { owner.failed = true; this.invalid = true; resolve(); });
    });
    this.owner = owner;
    return child;
  }

  observe(sessionId, query, message) {
    if (!this.enabled) return;
    if (sessionId !== this.owner?.sessionId || (this.query && query !== this.query)) {
      this.replaced = true;
      return;
    }
    if (!message || typeof message !== "object") { this.invalid = true; return; }
    const kind = message.type === "system" ? message.subtype : message.type;
    if (message.type === "system" && message.subtype === "background_tasks_changed") {
      this.epoch++;
      this.snapshot++;
      if (!Array.isArray(message.tasks) || message.tasks.length > this.maxTasks) {
        this.invalid = true; return;
      }
      const tasks = new Map();
      for (const task of message.tasks) {
        if (!idIsValid(task?.task_id) || !taskTypes.has(task.task_type) || tasks.has(task.task_id)) {
          this.invalid = true; return;
        }
        // Retain only identity/type, never native descriptions or command text.
        tasks.set(task.task_id, task.task_type);
      }
      this.tasks = tasks;
    } else if (kind === "active_goal") {
      this.epoch++;
      this.goalActive = message.value !== null;
    } else if (["hook_started", "hook_progress", "hook_response"].includes(kind)) {
      this.epoch++;
      if (!idIsValid(message.hook_id)) { this.invalid = true; return; }
      if (kind !== "hook_response") {
        if (!this.hooks.has(message.hook_id) && this.hooks.size >= 64) { this.invalid = true; return; }
        this.hooks.add(message.hook_id);
      } else this.hooks.delete(message.hook_id);
    } else if (["task_started", "task_notification", "task_updated", "task_progress", "tool_progress",
      "stream_event", "assistant", "user", "result", "session_state_changed", "api_retry",
      "command_lifecycle"].includes(kind)) {
      this.epoch++;
    }
  }

  stop(sessionId) {
    if (!this.enabled || sessionId !== this.sessionId) throw new Error("Unknown managed session");
    if (this.result) return this.result;
    this.sealed = true; // Permanent, even on failure or deadline expiry.
    this.result = this.drain(sessionId);
    return this.result;
  }

  async drain(sessionId) {
    const deadline = performance.now() + this.timeoutMs;
    const stopped = new Set();
    let stopAttempts = 0;
    let quiet = 0;
    let nativeExited = false;
    const receipt = (status, reason = null) => ({ ...capability, sessionId, status, reason,
      checkedNativeOwners: this.owner ? 1 : 0, stoppedTasks: stopped.size,
      quietObservations: quiet, nativeOwnerExited: nativeExited });
    const bounded = async (operation) => {
      const remaining = deadline - performance.now();
      if (remaining <= 0) throw new Error("timeout");
      let timer;
      try {
        return await Promise.race([Promise.resolve().then(operation), new Promise((_, reject) => {
          timer = setTimeout(() => reject(new Error("timeout")), remaining);
        })]);
      } finally { clearTimeout(timer); }
    };
    const owner = this.owner;
    const state = this.state(sessionId);
    const check = () => {
      if (this.replaced || this.owner !== owner || this.state(sessionId)?.query !== this.query) {
        throw new Error("native_owner_changed");
      }
      if (this.invalid || !owner || owner.failed) throw new Error("invalid_native_state");
      if (owner.exited) throw new Error("native_owner_exited_early");
      if (this.goalActive) throw new Error("active_native_goal");
    };
    try {
      check();
      state.startConsumer();
      await bounded(() => state.cancel());
      await bounded(() => Promise.allSettled([...this.pending]));
      check();
      const interrupt = await bounded(() => this.query.interrupt());
      if (!Array.isArray(interrupt?.still_queued) || interrupt.still_queued.length !== 0) {
        throw new Error("queued_work_unknown");
      }
      let previousEpoch = null;
      for (let pass = 0; pass < 100; pass++) {
        check();
        const beforeSnapshot = this.snapshot;
        const beforeEpoch = this.epoch;
        await bounded(() => this.query.reinitialize());
        await bounded(async () => {
          while (this.snapshot <= beforeSnapshot) {
            if (performance.now() >= deadline) throw new Error("timeout");
            check();
            await new Promise((resolve) => setTimeout(resolve, 10));
          }
        });
        check();
        if (this.tasks.size) {
          quiet = 0; previousEpoch = null;
          for (const id of this.tasks.keys()) {
            if (stopAttempts >= this.maxTasks) throw new Error("resource_limit");
            stopAttempts++;
            // Repeated IDs that remain alive cannot manufacture a quiet receipt.
            stopped.add(id);
            await bounded(() => this.query.stopTask(id));
          }
        } else if (this.hooks.size === 0) {
          // The snapshot itself contributes one event. Any other event invalidates quietness.
          if (this.epoch === beforeEpoch + 1 &&
              (previousEpoch === null || beforeEpoch === previousEpoch)) quiet++;
          else quiet = 0;
          previousEpoch = this.epoch;
          if (quiet >= 2) {
            check();
            const finalEpoch = this.epoch;
            // Closing input/query happens only after native work was observed quiet.
            await bounded(() => state.close());
            await bounded(() => owner.exit);
            nativeExited = owner.exited && !owner.failed;
            if (!nativeExited || this.replaced || this.invalid) throw new Error("native_exit_unknown");
            if (this.epoch !== finalEpoch + 1) throw new Error("native_work_after_quiet");
            return receipt("confirmed");
          }
        } else { quiet = 0; previousEpoch = null; }
        await bounded(() => new Promise((resolve) => setTimeout(resolve, 20)));
      }
      throw new Error("resource_limit");
    } catch (error) {
      const reasons = new Set(["timeout", "native_owner_changed", "invalid_native_state",
        "native_owner_exited_early", "active_native_goal", "queued_work_unknown",
        "resource_limit", "native_exit_unknown", "native_work_after_quiet"]);
      return receipt("uncertain", reasons.has(error?.message) ? error.message : "native_control_failed");
    }
  }
}
