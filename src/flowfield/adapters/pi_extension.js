// Loaded explicitly for a managed process; never installed into the user's Pi config.
export default function (pi) {
  const servers = JSON.parse(process.env.FLOWFIELD_PI_SERVERS || "[]");
  delete process.env.FLOWFIELD_PI_SERVERS;
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
