// Flowfield's small official Agent SDK host. The installed Claude Code owns tools,
// native settings and accounts. No CLI/SDK patches or ACP translator.
import { query } from "@anthropic-ai/claude-agent-sdk";
import { randomUUID } from "node:crypto";
import { createInterface } from "node:readline";
import { Cleanup } from "./cleanup.mjs";

let q, sessionId, consumer, promptResult, inputWaiter, inputClosed = false;
const queue = [], permissions = new Map();
let requestSequence = 0, messages = 0, bytes = 0;
let currentMessage = "text";
const streamed = new Set();
let initialized = false, started = false, stopping = false;
let turnError = null;
const bounded = async (operation, ms = 15000) => {
  let timer;
  try { return await Promise.race([operation(), new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error("deadline")), ms);
  })]); } finally { clearTimeout(timer); }
};
function send(message) {
  const frame = JSON.stringify(message);
  if (frame.length > 256 * 1024) throw new Error("output bound");
  if (!process.stdout.write(frame + "\n")) return new Promise(resolve => process.stdout.once("drain", resolve));
}
const emit = (event) => send({method: "activity", params: {sessionId, ...event}});
function endInput() { inputClosed = true; inputWaiter?.(); inputWaiter = null; }
const input = { async *[Symbol.asyncIterator]() {
  while (!inputClosed) {
    if (queue.length) yield queue.shift();
    else await new Promise(resolve => { inputWaiter = resolve; });
  }
} };
function cancelPermissions() {
  for (const waiter of permissions.values()) waiter({decision: "deny"});
  permissions.clear();
}
const cleanup = new Cleanup(id => id === sessionId && q ? {
  query: q, startConsumer() {},
  async cancel() { cancelPermissions(); },
  async close() { endInput(); q.close(); },
} : undefined);

async function permission(toolName, args, context) {
  if (stopping || !promptResult || context.signal.aborted) return {behavior: "deny", message: "Turn stopped"};
  if (permissions.size >= 16) throw new Error("permission bound");
  const id = "permission-" + (++requestSequence);
  const details = Object.fromEntries(["command", "cwd", "file_path", "path", "url"].filter(k => typeof args[k] === "string").map(k => [k, args[k].slice(0, 4000)]));
  let abort;
  const choice = await new Promise(resolve => {
    permissions.set(id, resolve);
    abort = () => { permissions.delete(id); resolve({decision: "deny"}); };
    context.signal.addEventListener("abort", abort, {once: true});
    send({id, method: "permission", params: {sessionId, toolId: context.toolUseID ?? id, title: toolName, details}});
  });
  context.signal.removeEventListener("abort", abort);
  permissions.delete(id);
  return choice.decision === "allow" && !stopping && !context.signal.aborted
    ? {behavior: "allow", updatedInput: args}
    : {behavior: "deny", message: "Permission denied or turn stopped"};
}
async function consume() {
  try {
    for await (const message of q) {
      cleanup.observe(sessionId, q, message);
      // Cleanup consumes every SDK event, including private reasoning; only public
      // allowlisted presentation crosses the application boundary.
      if (message.type === "system" && message.subtype === "init" && message.session_id !== sessionId) throw new Error("native identity changed");
      if (stopping && promptResult && message.type === "result") {
        const result = promptResult; promptResult = null; result.resolve({status: "stopped"});
      }
      if (stopping || !promptResult) continue;
      if (streamed.size > 256) throw new Error("stream bound");
      if (message.type === "stream_event") {
        const event = message.event;
        if (event.type === "message_start") currentMessage = event.message.id;
        if (event.type === "content_block_delta" && event.delta.type === "text_delta") {
          streamed.add(currentMessage + "-" + event.index);
          await emit({kind: "text", key: "agent-" + currentMessage + "-" + event.index, text: event.delta.text});
        } else if (event.type === "content_block_start" && event.content_block.type === "tool_use") {
          await emit({kind: "tool", key: event.content_block.id, title: event.content_block.name, status: "running"});
        }
      } else if (message.type === "assistant") {
        if (message.error) turnError = message.error === "authentication_failed" ? "authentication_failed" : "native_turn_failed";
        for (const [index, block] of (message.message.content ?? []).entries()) {
          if (block.type === "text" && !streamed.has(message.message.id + "-" + index)) {
            streamed.add(message.message.id + "-" + index);
            await emit({kind: "text", key: "agent-" + message.message.id + "-" + index, text: block.text});
          }
          if (block.type === "tool_use") {
            const details = Object.fromEntries(["command", "cwd", "file_path", "path"].filter(k => typeof block.input?.[k] === "string").map(k => [k, block.input[k].slice(0, 4000)]));
            await emit({kind: "tool", key: block.id, title: block.name, status: "running", details});
          }
        }
      } else if (message.type === "user") {
        for (const block of message.message?.content ?? []) {
          if (block.type === "tool_result") await emit({kind: "tool", key: block.tool_use_id, status: block.is_error ? "failed" : "completed"});
        }
      } else if (message.type === "system" && message.subtype === "local_command_output") {
        await emit({kind: "text", key: "command", text: message.content ?? ""});
      } else if (message.type === "system" && message.subtype === "compact_boundary") {
        await emit({kind: "text", key: "command", text: "Conversation compacted."});
      } else if (message.type === "result") {
        const usage = message.context_usage;
        if (usage) await emit({kind: "usage", used: usage.total_tokens, size: usage.raw_max_tokens});
        const result = promptResult; promptResult = null;
        const failed = message.subtype !== "success" || message.is_error !== false || turnError !== null;
        result.resolve({status: failed ? "failed" : "completed", ...(turnError ? {error: turnError} : {})});
      }
    }
    if (promptResult) { promptResult.reject(new Error("native process exited")); promptResult = null; }
  } catch {
    if (promptResult) { promptResult.reject(new Error("native consumer failed")); promptResult = null; }
    cleanup.invalid = true;
  }
}
async function info() {
  if (!q || !initialized || stopping) throw new Error("no native session");
  const context = await q.getContextUsage({detail: "summary"});
  const init = await q.initializationResult();
  if (!Array.isArray(init.models) || init.models.length > 64) throw new Error("catalog bound");
  return {sessionId, model: context.model,
    inputTokensAvailable: Number.isSafeInteger(context.maxTokens) && Number.isSafeInteger(context.totalTokens) && context.maxTokens > 0 && context.totalTokens >= 0 ? Math.max(0, context.maxTokens - context.totalTokens) : null,
    models: init.models.map(m => ({
    id: m.value, name: m.displayName, resolvedModel: m.resolvedModel ?? null,
    efforts: m.supportsEffort ? (m.supportedEffortLevels ?? []) : [],
    autoMode: m.supportsAutoMode === true,
  }))};
}
async function handle(method, params) {
  if (method === "start") {
    if (started || stopping) throw new Error("one native session per runtime");
    started = true;
    if (typeof params.executable !== "string" || typeof params.cwd !== "string" ||
        !["default", "acceptEdits", "auto", "bypassPermissions"].includes(params.mode)) throw new Error("invalid startup");
    sessionId = params.resume ?? randomUUID();
    await cleanup.attach(async () => {
      const options = {
        pathToClaudeCodeExecutable: params.executable, cwd: params.cwd,
        env: process.env, settingSources: ["user", "project", "local"],
        tools: {type: "preset", preset: "claude_code"},
        // Flowfield owns durable questions; managed Plan transitions are unsupported.
        // Exclude these natively so every access mode obeys the same boundary.
        disallowedTools: ["AskUserQuestion", "EnterPlanMode", "ExitPlanMode"],
        systemPrompt: {type: "preset", preset: "claude_code"},
        persistSession: params.persistent === true, strictMcpConfig: true,
        allowDangerouslySkipPermissions: params.mode === "bypassPermissions", permissionMode: params.mode,
        includePartialMessages: true, canUseTool: permission,
        mcpServers: params.servers ?? {},
        spawnClaudeCodeProcess: options => cleanup.spawn(sessionId, options),
        ...(params.resume ? {resume: params.resume} : {sessionId}),
        ...(params.model ? {model: params.model} : {}),
        ...(params.effort ? {effort: params.effort} : {}),
      };
      q = query({prompt: input, options});
      consumer = consume();
      consumer.catch(() => {});
      await q.initializationResult();
      initialized = true;
      return {sessionId};
    }, sessionId);
    return await info();
  }
  if (!q || params.sessionId !== sessionId) throw new Error("wrong native session");
  if (method === "stop") {
    stopping = true; cancelPermissions();
    return await cleanup.stop(sessionId);
  }
  if (stopping) throw new Error("native session stopped");
  if (method === "commands") {
    const commands = await q.supportedCommands();
    return {compact: commands.some(c => c.name === "compact" && c.builtin === true)};
  }
  if (method === "command") {
    if (promptResult) throw new Error("native turn already active");
    if (params.name === "status") {
      const context = await q.getContextUsage({detail: "summary"});
      return {text: [context.model, context.totalTokens == null ? null : context.totalTokens + " context tokens"].filter(Boolean).join(" · ")};
    }
    if (params.name === "mcp") {
      const servers = await q.mcpServerStatus();
      if (servers.length > 256) throw new Error("MCP inventory bound");
      return {text: servers.map(s => s.name + " · " + s.status).join("\n")};
    }
    if (params.name === "skills") {
      const skills = await q.supportedCommands();
      if (skills.length > 256) throw new Error("skill inventory bound");
      return {text: skills.filter(s => !s.builtin).map(s => s.name + (s.description ? " — " + s.description : "")).join("\n")};
    }
    throw new Error("unsupported command");
  }
  if (method === "info") return await info();
  if (method === "selectMode") {
    await q.setPermissionMode(params.mode);
    return {};
  }
  if (method === "selectModel") { await q.setModel(params.model); return await info(); }
  if (method === "prompt") {
    if (promptResult) throw new Error("native turn already active");
    const metadata = await info();
    if (stopping) return {status: "stopped"};
    if (promptResult) throw new Error("native turn already active");
    if (metadata.model !== params.model) throw new Error("native model changed");
    if (params.content?.length === 1 && params.content[0].text === "/compact") {
      const commands = await q.supportedCommands();
      if (!commands.some(c => c.name === "compact" && c.builtin === true)) throw new Error("native compact unavailable");
    }
    streamed.clear();
    turnError = null;
    const result = new Promise((resolve, reject) => { promptResult = {resolve, reject}; });
    queue.push({type: "user", uuid: randomUUID(), session_id: sessionId, parent_tool_use_id: null,
      message: {role: "user", content: params.content}});
    inputWaiter?.(); inputWaiter = null;
    return await result;
  }
  throw new Error("unknown operation");
}
const lines = createInterface({input: process.stdin, crlfDelay: Infinity});
const jobs = new Set();
lines.on("line", line => {
  bytes += line.length; messages++;
  if (line.length > 24 * 1024 * 1024 || bytes > 64 * 1024 * 1024 || messages > 100000) { lines.close(); return; }
  let message;
  try { message = JSON.parse(line); } catch { lines.close(); return; }
  if (typeof message.id === "string" && !message.method) { permissions.get(message.id)?.(message.result ?? {decision: "deny"}); return; }
  if (!Number.isSafeInteger(message.id) || typeof message.method !== "string" || jobs.size >= 16) { lines.close(); return; }
  const job = (async () => {
    try {
      const result = await (message.method === "prompt" ? handle(message.method, message.params ?? {}) : bounded(() => handle(message.method, message.params ?? {}), 20000));
      await send({id: message.id, result});
    } catch { await send({id: message.id, error: {code: -32603, message: "Native operation failed; nothing was replayed"}}); }
  })();
  jobs.add(job); job.finally(() => jobs.delete(job));
});
lines.on("close", async () => {
  stopping = true; cancelPermissions();
  try { if (cleanup.sessionId) await cleanup.stop(sessionId); } catch {}
  endInput(); q?.close();
  // EOF may arrive after Python observed an uncertain receipt. Escalate only the
  // exact SDK child; its exit never manufactures background-work confirmation.
  if (cleanup.owner && !cleanup.owner.exited) cleanup.owner.child.kill("SIGTERM");
});
