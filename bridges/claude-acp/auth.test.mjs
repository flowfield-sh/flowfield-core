// Requires the explicit proof build; never spawns a native CLI or calls a model.
import assert from "node:assert/strict";
import test from "node:test";
import { billsClaudeSubscription, holdsNonSubscriptionCredential } from "./.work/upstream/dist/hide-claude-auth.js";

test("the released CLI's signed-out sentinel cannot establish authentication", () => {
  assert.equal(holdsNonSubscriptionCredential({ apiProvider: "firstParty", tokenSource: "none" }), false);
  assert.equal(holdsNonSubscriptionCredential({ apiProvider: "firstParty" }), false);
});

test("the sentinel cannot hide subscription billing", () => {
  const account = { apiProvider: "firstParty", subscriptionType: "pro", tokenSource: "none" };
  assert.equal(billsClaudeSubscription(account), true);
  assert.equal(holdsNonSubscriptionCredential(account), false);
});

test("the correction retains native API, Console and external-provider routes", () => {
  for (const apiKeySource of ["ANTHROPIC_API_KEY", "apiKeyHelper", "/login managed key"]) {
    const account = { apiProvider: "firstParty", apiKeySource, tokenSource: "none", subscriptionType: "pro" };
    assert.equal(holdsNonSubscriptionCredential(account), true);
    assert.equal(billsClaudeSubscription(account), false);
  }
  assert.equal(holdsNonSubscriptionCredential({ apiProvider: "bedrock", tokenSource: "none" }), true);
  // This is the bridge's classification, not proof of billing eligibility.
  assert.equal(holdsNonSubscriptionCredential({ apiProvider: "firstParty", tokenSource: "oauth" }), true);
});
