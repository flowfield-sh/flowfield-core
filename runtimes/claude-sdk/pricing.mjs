// Supplemental numeric evidence only. Never store message contents or use the
// assistant's placeholder output count; modelUsage supplies authoritative totals.
export class Pricing {
  constructor() {
    this.messages = new Map();
  }
  observe(event) {
    const message =
      event.type === "assistant"
        ? event.message
        : event.type === "stream_event" && event.event?.type === "message_start"
          ? event.event.message
          : null;
    if (
      !message ||
      typeof message.id !== "string" ||
      typeof message.model !== "string" ||
      message.id.length > 200 ||
      message.model.length > 200 ||
      (!this.messages.has(message.id) && this.messages.size >= 4096)
    )
      return;
    const u = message.usage,
      valid = (n) => Number.isSafeInteger(n) && n >= 0;
    if (
      !u ||
      ![
        u.input_tokens,
        u.cache_read_input_tokens,
        u.cache_creation_input_tokens,
      ].every(valid)
    )
      return;
    const c = u.cache_creation;
    const short = c?.ephemeral_5m_input_tokens,
      long = c?.ephemeral_1h_input_tokens;
    const previous = this.messages.get(message.id);
    this.messages.set(message.id, {
      model: message.model,
      input:
        u.input_tokens +
        u.cache_read_input_tokens +
        u.cache_creation_input_tokens,
      short: valid(short) ? short : (previous?.short ?? null),
      long: valid(long) ? long : (previous?.long ?? null),
    });
  }
  forModel(model, usage) {
    const messages = [...this.messages.values()].filter(
      (m) => m.model === model || m.model === usage.canonicalModel,
    );
    const writes = (key) =>
      messages.length && messages.every((m) => m[key] !== null)
        ? messages.reduce((n, m) => n + m[key], 0)
        : null;
    return {
      model: usage.canonicalModel ?? model,
      provider: usage.provider ?? null,
      cache_write_short: writes("short"),
      cache_write_long: writes("long"),
      input_observed: messages.length
        ? messages.reduce((n, m) => n + m.input, 0)
        : null,
      max_input: messages.length
        ? Math.max(...messages.map((m) => m.input))
        : null,
      service_tier: "standard",
    };
  }
  clear() {
    this.messages.clear();
  }
}
