import { test } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { mkdtempSync, rmSync, existsSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { startService } from "./service.ts";

const info = (directory) => ({
  outputDir: directory,
  outputPath: (name) => join(directory, name),
  status: "passed",
  expectedStatus: "passed",
  attach: async () => {},
});

test("simultaneous owners have distinct stores/ports and clean up only their own store", async () => {
  const output = mkdtempSync(join(tmpdir(), "flowfield-isolation-proof-"));
  let services = [];
  try {
    const started = await Promise.allSettled([
      startService(info(join(output, "a"))),
      startService(info(join(output, "b"))),
    ]);
    services = started
      .filter((result) => result.status === "fulfilled")
      .map((result) => result.value);
    for (const result of started)
      if (result.status === "rejected") throw result.reason;
    const [first, second] = services;
    assert.notEqual(first.port, second.port);
    assert.notEqual(first.state, second.state);
    await first.stop();
    assert.equal(existsSync(first.state), false);
    assert.equal((await fetch(second.baseURL + "/api/health")).status, 200);
    await second.stop();
    assert.equal(existsSync(second.state), false);
  } finally {
    await Promise.all(services.map((service) => service.stop()));
    rmSync(output, { recursive: true, force: true });
  }
});

test("service stops when its worker is killed and retains the interrupted store", async () => {
  const output = mkdtempSync(join(tmpdir(), "flowfield-owner-proof-"));
  const script = `import { startService } from './e2e/service.ts';
    const directory = process.argv[1];
    const s = await startService({ outputDir: directory, outputPath: n => directory + '/' + n,
      status: 'passed', expectedStatus: 'passed', attach: async () => {} });
    process.send({state:s.state,url:s.baseURL}); setInterval(()=>{},1000);`;
  const worker = spawn(
    process.execPath,
    ["--input-type=module", "-e", script, output],
    { stdio: ["ignore", "ignore", "pipe", "ipc"] },
  );
  const exited = once(worker, "exit");
  let diagnostics = "";
  worker.stderr.on("data", (chunk) => {
    diagnostics = (diagnostics + chunk.toString()).slice(-8000);
  });
  let state;
  let stopped = false;
  try {
    const result = await Promise.race([
      once(worker, "message", { signal: AbortSignal.timeout(35000) }).then(
        ([message]) => message,
      ),
      exited.then(([code, signal]) => {
        throw new Error(
          `Worker exited before readiness (${code ?? signal}): ${diagnostics}`,
        );
      }),
    ]);
    state = result.state;
    const { pid } = JSON.parse(
      readFileSync(join(state, "address.json"), "utf8"),
    );
    worker.kill("SIGKILL");
    await exited;
    const deadline = Date.now() + 10000;
    while (Date.now() < deadline) {
      try {
        process.kill(pid, 0);
      } catch (error) {
        if (error.code !== "ESRCH") throw error;
        stopped = true;
        break;
      }
      await new Promise((done) => setTimeout(done, 100));
    }
    assert.ok(stopped, "orphaned service did not stop");
    assert.ok(existsSync(state), "interrupted evidence must remain");
  } finally {
    worker.kill("SIGKILL");
    await exited.catch(() => {});
    if (state && stopped) rmSync(state, { recursive: true, force: true });
    rmSync(output, { recursive: true, force: true });
  }
});
