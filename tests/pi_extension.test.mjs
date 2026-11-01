import {test} from "node:test";
import assert from "node:assert/strict";
import extension from "../src/flowfield/adapters/pi_extension.js";

test("native prompt sections preserve configured guidance and rebind on resume", () => {
  function bind(instructions) {
    const events = new Map();
    process.env.FLOWFIELD_PI_SERVERS = "[]";
    process.env.FLOWFIELD_PI_INSTRUCTIONS = instructions;
    extension({on(name, callback) { events.set(name, callback); }});
    assert.equal(process.env.FLOWFIELD_PI_INSTRUCTIONS, undefined);
    return events;
  }
  const events = bind("Current revision one");
  for (let turn = 0; turn < 3; turn++) {
    // Pi rebuilds structured options from the resource loader for each run.
    const event = {systemPromptOptions: {appendSystemPrompt: "Configured native appendix", customSystemPrompt: "Native system"}};
    events.get("before_agent_start")(event);
    assert.deepEqual(event.systemPromptOptions, {
      appendSystemPrompt: "Configured native appendix\n\nCurrent revision one", customSystemPrompt: "Native system",
    });
    assert.equal(event.systemPrompt, undefined); // Do not force/replace the native prompt.
  }
  const resumed = bind("Current revision two");
  const event = {systemPromptOptions: {appendSystemPrompt: "Configured native appendix"}};
  resumed.get("before_agent_start")(event);
  assert.equal(event.systemPromptOptions.appendSystemPrompt, "Configured native appendix\n\nCurrent revision two");
  assert.equal(bind("").has("before_agent_start"), false);
});
