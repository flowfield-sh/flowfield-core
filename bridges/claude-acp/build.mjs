// An isolated capability proof, not a runtime installation or selectable adapter.
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { cp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));
const spec = JSON.parse(await readFile(join(root, "proof.json"), "utf8"));
const work = join(root, ".work");
const source = join(work, "upstream");
const compiler = process.env.FLOWFIELD_BUILD_BUN;
if (!compiler || !isAbsolute(compiler)) {
  throw new Error("Set FLOWFIELD_BUILD_BUN to the absolute Bun compiler path");
}
if (execFileSync(compiler, ["--version"], { encoding: "utf8" }).trim() !== spec.bun_version) {
  throw new Error("Unexpected Bun compiler version");
}
if (!["darwin", "linux"].includes(process.platform) || !["arm64", "x64"].includes(process.arch)) {
  throw new Error("This proof currently builds POSIX arm64/x64 only");
}
const archive = process.argv[2]
  ? await readFile(resolve(process.argv[2]))
  : await (async () => {
      const response = await fetch(`https://api.github.com/repos/agentclientprotocol/claude-agent-acp/tarball/${spec.upstream_revision}`);
      if (!response.ok) throw new Error(`Source download failed: ${response.status}`);
      return Buffer.from(await response.arrayBuffer());
    })();
function check(bytes, expected) {
  if (createHash("sha256").update(bytes).digest("hex") !== expected) {
    throw new Error("Source checksum mismatch; review before rebuilding");
  }
}
check(archive, spec.archive_sha256);
await mkdir(work, { recursive: true });
await writeFile(join(work, "upstream.tar.gz"), archive);
await rm(source, { recursive: true, force: true });
await mkdir(source);
execFileSync("tar", ["-xzf", join(work, "upstream.tar.gz"), "-C", source, "--strip-components=1"]);
check(await readFile(join(source, "package-lock.json")), spec.lock_sha256);
const pkg = JSON.parse(await readFile(join(source, "package.json"), "utf8"));
if (pkg.version !== spec.upstream_version ||
    pkg.dependencies["@agentclientprotocol/sdk"] !== spec.acp_sdk_version ||
    pkg.dependencies["@anthropic-ai/claude-agent-sdk"] !== spec.agent_sdk_version) {
  throw new Error("Unexpected released dependency pins");
}
// CLI 2.1.295 emits tokenSource:"none" while signed out. Upstream's guard
// mistakes that sentinel for a usable credential. Keep this bounded patch here.
const guardFile = join(source, "src/hide-claude-auth.ts");
let guard = await readFile(guardFile, "utf8");
const checkToken = "if (account.tokenSource) {";
if (guard.split(checkToken).length !== 3) throw new Error("Unexpected authentication guard source");
guard = "// Modified by Flowfield: reject the native signed-out token sentinel.\n" +
  guard.replaceAll(checkToken, 'if (account.tokenSource && account.tokenSource !== "none") {');
await writeFile(guardFile, guard);
pkg.version = spec.proof_version;
await writeFile(join(source, "package.json"), JSON.stringify(pkg, null, 2) + "\n");
execFileSync("npm", ["ci", "--ignore-scripts", "--no-audit", "--no-fund"], { cwd: source, stdio: "inherit" });
execFileSync("npm", ["run", "build"], { cwd: source, stdio: "inherit" });
execFileSync(process.execPath, ["--test", join(root, "auth.test.mjs")], { cwd: source, stdio: "inherit" });
await cp(join(root, "native-control.mjs"), join(source, "src/flowfield-native-control.mjs"));
await writeFile(join(source, "src/flowfield-proof.ts"), `
export {};
import { isAbsolute } from "node:path";
if (!process.env.CLAUDE_CODE_EXECUTABLE || !isAbsolute(process.env.CLAUDE_CODE_EXECUTABLE)) {
  throw new Error("This proof requires an explicit absolute native executable");
}
// Raw bridge logs can include native/project data; this proof never records them.
delete process.env.CLAUDE_AGENT_LOGS;
if (process.argv.includes("--flowfield-proof-native-control")) {
  await import("./flowfield-native-control.mjs");
} else {
  await import("./index.js");
}
`);
execFileSync(compiler, ["build", "--compile", "--minify",
  "--no-compile-autoload-dotenv", "--no-compile-autoload-bunfig",
  "--no-compile-autoload-tsconfig", "--no-compile-autoload-package-json",
  `--target=bun-${process.platform}-${process.arch}`, "src/flowfield-proof.ts",
  "--outfile", join(work, "claude-acp-proof")], { cwd: source, stdio: "inherit" });
console.log(join(work, "claude-acp-proof"));
