// Loaded explicitly for a managed process; never installed into the user's Pi config.
export default function (pi) {
  const servers = JSON.parse(process.env.FLOWFIELD_PI_SERVERS || "[]");
  delete process.env.FLOWFIELD_PI_SERVERS;
  const instructions = process.env.FLOWFIELD_PI_INSTRUCTIONS || "";
  delete process.env.FLOWFIELD_PI_INSTRUCTIONS;
  if (instructions) {
    pi.on("before_agent_start", (event) => {
      // Add a native prompt section: preserve configured SYSTEM/APPEND_SYSTEM
      // files and let Pi retain/diff sections across turns, resume and compaction.
      const options = event.systemPromptOptions;
      options.appendSystemPrompt = [options.appendSystemPrompt, instructions].filter(Boolean).join("\n\n");
    });
  }
  for (const server of servers) {
    pi.registerMcpServer(server.name, {
      ...server.config,
      exposure: "direct",
      timeout: 60,
    });
  }
  pi.on("session_start", (_event, ctx) => {
    ctx.ui.notify("flowfield:ready:v1", "info");
  });
  pi.on("session_shutdown", (_event, ctx) => {
    ctx.ui.notify("flowfield:closed:v1", "info");
  });
}
