import { readFileSync } from "node:fs";
import { test } from "node:test";
import assert from "node:assert/strict";
import { consumption } from "./usage.mjs";
const scope = {sessionId: "s", nativeVersion: "2.1.293", resumed: false, order: 1};
const result = {type: "result", session_id: "s", uuid: "r", subtype: "success", is_error: false,
  modelUsage: {m: {inputTokens: 100, outputTokens: 20, cacheReadInputTokens: 60, cacheCreationInputTokens: 10, thinkingTokens: 8, costUSD: 5, private: "secret"}},
  usage: {input_tokens: 999}, content: "private reasoning"};
test("allowlist cumulative authoritative scope excludes main-loop/native-child double counting", () => {
  const [value] = consumption(result, scope);
  assert.deepEqual(value.counters, {input: 100, output: 20, cache_read: 60, cache_write: 10, reasoning: 8});
  assert.equal(value.final, true);
  assert.equal(JSON.stringify(value).includes("secret"), false);
  assert.equal(JSON.stringify(value).includes("cost"), false);
  assert.equal(JSON.stringify(value).includes("999"), false);
});
test("missing, negative, default-like invalid and error fields are never invented zero", () => {
  const [value] = consumption({...result, modelUsage: {m: {inputTokens: -1, outputTokens: false}}}, scope);
  assert.equal(value.counters.input, null);
  assert.equal(value.counters.output, null);
  assert.equal(value.gap, "missing-counters");
  const [error] = consumption({...result, is_error: true}, scope);
  assert.equal(error.gap, "native-error"); assert.equal(error.final, false);
});
test("resumed totals require durable baseline and scope identity", () => {
  assert.equal(consumption(result, {...scope, resumed: true})[0].baseline, "resume");
  assert.deepEqual(consumption({...result, session_id: "other"}, scope), []);
  assert.deepEqual(consumption({...result, type: "assistant"}, scope), []);
});
test("models stay distinct and reasoning is optional output subset", () => {
  const output = consumption({...result, modelUsage: {a: result.modelUsage.m, b: {inputTokens: 0, outputTokens: 0, cacheReadInputTokens: 0, cacheCreationInputTokens: 0}}}, scope);
  assert.equal(output.length, 2); assert.equal(output[1].counters.reasoning, null);
  assert.equal(output[1].counters.input, 0);
});

test("other native versions remain explicitly unavailable", () => {
  const [value] = consumption(result, {...scope, nativeVersion: "2.0.0"});
  assert.equal(value.gap, "unsupported-version"); assert.equal(value.final, false);
});

test("pinned primary interface remains compatible; order is native or unknown", () => {
  const pkg = JSON.parse(readFileSync(new URL("./node_modules/@anthropic-ai/claude-agent-sdk/package.json", import.meta.url)));
  assert.equal(pkg.version, "0.3.293"); assert.equal(consumption(result, {...scope, nativeVersion: pkg.claudeCodeVersion})[0].gap, null);
  assert.equal(consumption(result, scope)[0].order, null);
  assert.equal(consumption({...result, result_index: 7}, scope)[0].order, 7);
});

for (const version of ["2.1.277", "2.1.293", "2.1.295", "2.2.0", "3.0.0", "9.0.0-beta.1+build.7"]) {
  test("compatible native release " + version + " retains the same cumulative contract", () => {
    const [value] = consumption({...result, futureField: true, modelUsage: {m: {...result.modelUsage.m, futureCounter: 999}}}, {...scope, nativeVersion: version, resumed: true});
    assert.equal(value.gap, null); assert.equal(value.final, true);
    assert.equal(value.baseline, "resume"); assert.equal(value.native_version, version);
    assert.equal(value.contract, consumption(result, scope)[0].contract);
    assert.deepEqual(value.counters, consumption(result, scope)[0].counters);
  });
}
for (const version of ["2.1.276", "2.0.999", "1.9.9", null, "unknown", "2.1", "2.1.295garbage"]) {
  test("unknown or pre-session-total semantics remain unavailable: " + version, () => {
    const [value] = consumption(result, {...scope, nativeVersion: version});
    assert.equal(value.gap, "unsupported-version"); assert.equal(value.final, false);
  });
}
for (const modelUsage of [null, [], "changed", {m: []}]) {
  test("invalid modelUsage container " + JSON.stringify(modelUsage), () => {
    assert.deepEqual(consumption({...result, modelUsage}, {...scope, nativeVersion: "9.0.0"}), []);
  });
}
