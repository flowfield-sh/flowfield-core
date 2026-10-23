import { spawn } from "node:child_process";
import { once } from "node:events";
import {
  mkdtempSync,
  readFileSync,
  existsSync,
  rmSync,
  mkdirSync,
  openSync,
  closeSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import type { TestInfo } from "@playwright/test";

export async function startService(info: TestInfo) {
  const state = mkdtempSync(join(tmpdir(), "flowfield-browser-"));
  const address = join(state, "address.json");
  mkdirSync(info.outputDir, { recursive: true });
  const log = info.outputPath("service.log");
  const fd = openSync(log, "w");
  const child = spawn(
    "uv",
    [
      "run",
      "--no-sync",
      "--project",
      resolve(".."),
      "python",
      resolve("e2e/server.py"),
      state,
      address,
      String(process.pid),
    ],
    {
      detached: true,
      stdio: ["ignore", fd, fd],
      env: {
        ...process.env,
        FLOWFIELD_UPDATE_CHECKS: "0",
        FLOWFIELD_DATA_DIR: state,
      },
    },
  );
  closeSync(fd);
  const exited = once(child, "exit");
  // Observe spawn failures immediately, including while readiness is polling.
  let failure: Error | undefined;
  child.on("error", (error) => {
    failure = error;
  });
  void exited.catch(() => {});
  function signalGroup(signal: NodeJS.Signals) {
    try {
      process.kill(-child.pid!, signal);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ESRCH") throw error;
    }
  }
  async function stop(retain = false) {
    let forced = false;
    if (child.pid && child.exitCode === null && child.signalCode === null) {
      signalGroup("SIGTERM");
      let timer: ReturnType<typeof setTimeout>;
      await Promise.race([
        exited,
        new Promise<void>((done) => {
          timer = setTimeout(() => {
            forced = true;
            signalGroup("SIGKILL");
            done();
          }, 10000);
        }),
      ]).finally(() => clearTimeout(timer!));
      await exited;
    }
    if (
      retain ||
      forced ||
      process.env.FLOWFIELD_SMOKE_RETAIN === "1" ||
      info.status !== info.expectedStatus
    ) {
      await info.attach("service-state", {
        body: state,
        contentType: "text/plain",
      });
    } else rmSync(state, { recursive: true, force: true });
    if (forced)
      throw new Error(
        `Service cleanup timed out; retained ${state}. See ${log}`,
      );
  }
  try {
    const deadline = Date.now() + 30000;
    while (Date.now() < deadline) {
      if (failure) throw failure;
      if (child.exitCode !== null || child.signalCode !== null)
        throw new Error(
          `Service exited before readiness: ${readFileSync(log, "utf8")}`,
        );
      if (existsSync(address)) {
        const { port } = JSON.parse(readFileSync(address, "utf8")) as {
          port: number;
        };
        const baseURL = `http://127.0.0.1:${port}`;
        try {
          if (
            (
              await fetch(`${baseURL}/api/health`, {
                signal: AbortSignal.timeout(1000),
              })
            ).ok
          )
            return { state, port, baseURL, stop };
        } catch {
          /* The socket is bound before application startup completes. */
        }
      }
      await new Promise((done) => setTimeout(done, 50));
    }
    throw new Error(`Service readiness timed out. See ${log}`);
  } catch (error) {
    await stop(true);
    throw error;
  }
}
