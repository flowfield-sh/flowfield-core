// ModelUsage/SDKResultMessage contract. Only query-pipeline
// totals are authoritative: never add per-step, main-loop or native child usage.
// Before 2.1.277, resumed queries reset these totals. Later compatible releases
// retain session totals. Keep the original contract ID so durable baselines survive
// native upgrades; it identifies the measured schema, not a release allowlist.
function sessionTotals(version) {
  const match = typeof version === "string" && /^(\d+)\.(\d+)\.(\d+)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/.exec(version);
  if (!match) return false;
  const [major, minor, patch] = match.slice(1, 4).map(Number);
  if (![major, minor, patch].every(Number.isSafeInteger)) return false;
  return major > 2 || (major === 2 && (minor > 1 || (minor === 1 && patch >= 277)));
}
const fields = {inputTokens: "input", outputTokens: "output", cacheReadInputTokens: "cache_read", cacheCreationInputTokens: "cache_write", thinkingTokens: "reasoning"};
export function consumption(message, {sessionId, nativeVersion, resumed, pricing}) {
  if (message.type !== "result" || message.session_id !== sessionId ||
      typeof message.uuid !== "string" || message.uuid.length > 200 || !message.uuid ||
      !message.modelUsage || typeof message.modelUsage !== "object" || Array.isArray(message.modelUsage)) return [];
  const entries = Object.entries(message.modelUsage);
  if (entries.length > 64) return [];
  return entries.filter(([model, usage]) => model.length > 0 && model.length <= 200 && usage && typeof usage === "object" && !Array.isArray(usage)).map(([model, usage]) => {
    const counters = Object.fromEntries(Object.entries(fields).map(([native, field]) => [field,
      Number.isSafeInteger(usage[native]) && usage[native] >= 0 ? usage[native] : null]));
    const failed = message.subtype !== "success" || message.is_error !== false;
    const compatible = sessionTotals(nativeVersion);
    return {event_id: message.uuid, session_id: sessionId,
      order: Number.isSafeInteger(message.result_index) && message.result_index >= 0 ? message.result_index : null, model, counters,
      contract: "claude-model-usage/0.3.293", native_version: nativeVersion,
      ...(pricing ? {pricing: pricing.forModel(model, usage)} : {}),
      baseline: resumed ? "resume" : "fresh", semantics: "disjoint-cache",
      final: !failed && compatible, gap: !compatible ? "unsupported-version" : failed ? "native-error" :
        ["input", "output", "cache_read", "cache_write"].some(k => counters[k] === null) ? "missing-counters" : null};
  });
}
