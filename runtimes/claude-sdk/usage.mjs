// Pinned SDK 0.3.293 ModelUsage/SDKResultMessage contract. Only query-pipeline
// totals are authoritative: never add per-step, main-loop or native child usage.
export const nativeUsageVersion = "2.1.293";
const fields = {inputTokens: "input", outputTokens: "output", cacheReadInputTokens: "cache_read", cacheCreationInputTokens: "cache_write", thinkingTokens: "reasoning"};
export function consumption(message, {sessionId, nativeVersion, resumed}) {
  if (message.type !== "result" || message.session_id !== sessionId ||
      typeof message.uuid !== "string" || message.uuid.length > 200 || !message.uuid ||
      !message.modelUsage || typeof message.modelUsage !== "object" || Array.isArray(message.modelUsage)) return [];
  const entries = Object.entries(message.modelUsage);
  if (entries.length > 64) return [];
  return entries.filter(([model, usage]) => model.length > 0 && model.length <= 200 && usage && typeof usage === "object").map(([model, usage]) => {
    const counters = Object.fromEntries(Object.entries(fields).map(([native, field]) => [field,
      Number.isSafeInteger(usage[native]) && usage[native] >= 0 ? usage[native] : null]));
    const failed = message.subtype !== "success" || message.is_error !== false;
    return {event_id: message.uuid, session_id: sessionId,
      order: Number.isSafeInteger(message.result_index) && message.result_index >= 0 ? message.result_index : null, model, counters,
      contract: "claude-model-usage/0.3.293", native_version: nativeVersion,
      baseline: resumed ? "resume" : "fresh", semantics: "disjoint-cache",
      final: !failed && nativeVersion === nativeUsageVersion, gap: nativeVersion !== nativeUsageVersion ? "unsupported-version" : failed ? "native-error" :
        ["input", "output", "cache_read", "cache_write"].some(k => counters[k] === null) ? "missing-counters" : null};
  });
}
