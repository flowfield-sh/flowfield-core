import { execFileSync } from "node:child_process";
import { mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
const root = dirname(fileURLToPath(import.meta.url));
const compiler = process.env.FLOWFIELD_BUILD_BUN;
if (!compiler || execFileSync(compiler, ["--version"], {encoding: "utf8"}).trim() !== "1.3.11") throw new Error("Set FLOWFIELD_BUILD_BUN to Bun 1.3.11");
const pkg = JSON.parse(await readFile(join(root, "node_modules/@anthropic-ai/claude-agent-sdk/package.json")));
if (pkg.version !== "0.3.293") throw new Error("Unexpected SDK version");
const all = ["darwin-arm64", "linux-arm64", "linux-x64"];
const targets = process.argv.includes("--host") ? [`${process.platform}-${process.arch}`] : all;
let notices = "Flowfield Claude SDK runtime\n\n" + await readFile(join(root, "../../LICENSE"), "utf8") + "\n\n" + await readFile(join(root, "compiler-LICENSE.md"), "utf8");
const store = join(root, "node_modules/.pnpm");
for (const entry of await readdir(store, {withFileTypes: true})) {
  if (!entry.isDirectory() || entry.name === "node_modules") continue;
  const modules = join(store, entry.name, "node_modules");
  async function collect(directory) {
    for (const name of await readdir(directory)) {
      if (name.startsWith("@")) { await collect(join(directory, name)); continue; }
      if (name.startsWith(".")) continue;
      const location = join(directory, name);
      try {
        const metadata = JSON.parse(await readFile(join(location, "package.json")));
        notices += `\n\n${metadata.name} ${metadata.version}\n`;
        for (const file of await readdir(location)) {
          if (/^(license|copying|notice)(\.|$)/i.test(file)) notices += await readFile(join(location, file), "utf8") + "\n";
        }
      } catch {}
    }
  }
  await collect(modules);
}
for (const target of targets) {
  if (!all.includes(target)) throw new Error("Unsupported target");
  const out = resolve(root, `../../src/flowfield/_native/claude-sdk-${target}`);
  await mkdir(out, {recursive: true});
  execFileSync(compiler, ["build", "--compile", "--minify", `--target=bun-${target}${target.endsWith("-x64") ? "-baseline" : ""}`, "--outfile", join(out, "claude-sdk"), join(root, "runtime.mjs")], {stdio: "inherit"});
  await writeFile(join(out, "THIRD_PARTY_NOTICES.txt"), notices);
  await writeFile(join(out, "version.json"), JSON.stringify({version: 1, sdk: pkg.version, compiler: "1.3.11", target}));
}
