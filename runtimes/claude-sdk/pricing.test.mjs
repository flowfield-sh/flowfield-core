import { test } from "node:test";
import assert from "node:assert/strict";
import { Pricing } from "./pricing.mjs";
import { consumption } from "./usage.mjs";
const message = {
  id: "m1",
  model: "claude-sonnet-5-5",
  content: "private",
  usage: {
    input_tokens: 100,
    cache_read_input_tokens: 50,
    cache_creation_input_tokens: 10,
    output_tokens: 1,
    cache_creation: {
      ephemeral_5m_input_tokens: 6,
      ephemeral_1h_input_tokens: 4,
    },
  },
};
test("assistant and streaming duplicates preserve numeric request evidence only", () => {
  const p = new Pricing();
  p.observe({
    type: "stream_event",
    event: { type: "message_start", message },
  });
  p.observe({ type: "assistant", message });
  const data = p.forModel("claude-sonnet-5-5", { provider: "firstParty" });
  assert.equal(data.input_observed, 160);
  assert.equal(data.cache_write_short, 6);
  assert.equal(data.cache_write_long, 4);
  assert.equal(JSON.stringify(data).includes("private"), false);
  const [result] = consumption(
    {
      type: "result",
      session_id: "s",
      uuid: "r",
      subtype: "success",
      is_error: false,
      modelUsage: {
        "claude-sonnet-5-5": {
          provider: "firstParty",
          inputTokens: 100,
          outputTokens: 20,
          cacheReadInputTokens: 50,
          cacheCreationInputTokens: 10,
        },
      },
    },
    { sessionId: "s", nativeVersion: "2.1.295", resumed: false, pricing: p },
  );
  assert.equal(result.counters.output, 20);
  assert.deepEqual(result.pricing, data);
  p.clear();
  assert.equal(p.forModel("claude-sonnet-5-5", {}).input_observed, null);
});
test("missing cache lifetimes and unobserved helper requests remain unavailable", () => {
  const p = new Pricing();
  p.observe({
    type: "assistant",
    message: {
      ...message,
      usage: { ...message.usage, cache_creation: undefined },
    },
  });
  assert.equal(p.forModel(message.model, {}).cache_write_long, null);
  assert.equal(p.forModel("claude-haiku-5-5", {}).input_observed, null);
});
test("canonical model identity keeps individual model usage separate", () => {
  const p = new Pricing();
  p.observe({ type: "assistant", message });
  assert.equal(
    p.forModel("alias", { canonicalModel: message.model }).input_observed,
    160,
  );
  assert.equal(p.forModel("alias", {}).input_observed, null);
});

test("missing provider identity is never assumed first-party", () => {
  assert.equal(new Pricing().forModel(message.model, {}).provider, null);
});
