import { spawn } from "node:child_process";
import { rmSync } from "node:fs";

// The service must release workers and callbacks before its database disappears.
// Playwright's global teardown runs before webServer shutdown, so this owner
// performs cleanup only after the service exits. Forced termination retains state.
const service = spawn(
  "uv",
  [
    "run",
    "--project",
    "..",
    "flowfield",
    "serve",
    "--no-open",
    "--port",
    process.env.FLOWFIELD_SMOKE_PORT ?? "8766",
  ],
  {
    stdio: "inherit",
  },
);
let stoppingCode;
for (const [signal, code] of [
  ["SIGTERM", 143],
  ["SIGINT", 130],
]) {
  // Playwright signals the whole process group, including uv and the service.
  // Do not forward it a second time and force an otherwise graceful shutdown.
  process.on(signal, () => {
    stoppingCode = code;
  });
}
service.on("error", (error) => {
  console.error(error);
  process.exitCode = 1;
});
service.on("exit", (code, signal) => {
  if (
    (code === 0 || code === stoppingCode) &&
    !signal &&
    process.env.FLOWFIELD_SMOKE_CLEANUP === "true" &&
    process.env.FLOWFIELD_SMOKE_STATE
  ) {
    rmSync(process.env.FLOWFIELD_SMOKE_STATE, {
      recursive: true,
      force: true,
      maxRetries: 5,
      retryDelay: 100,
    });
  }
  process.exitCode = code === stoppingCode ? 0 : (code ?? 1);
});
