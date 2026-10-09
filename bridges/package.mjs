// Package a checked native executable; no JavaScript runtime is needed by users.
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { readFile, writeFile, mkdir, readdir } from "node:fs/promises";
import { join, resolve } from "node:path";

export async function packageBridge({ root, spec, name, label, upstream }) {
  const source = join(root, ".work/upstream");
  const bun = process.env.FLOWFIELD_BUILD_BUN || "bun";
  if (
    execFileSync(bun, ["--version"], { encoding: "utf8" }).trim() !==
    spec.bun_version
  )
    throw new Error(`Build with Bun ${spec.bun_version}`);
  const platform = `${process.platform}-${process.arch}`;
  if (
    !["darwin-arm64", "darwin-x64", "linux-arm64", "linux-x64"].includes(
      platform,
    )
  )
    throw new Error("Standalone builds require macOS or Linux on arm64/x64");
  const output = join(root, ".work/packages", platform);
  await mkdir(output, { recursive: true });
  // Use the installed native harness; no npm wrapper is needed at runtime.
  const entry = join(source, "src/flowfield-standalone.ts");
  execFileSync(
    bun,
    [
      "build",
      "--compile",
      "--minify",
      "--no-compile-autoload-dotenv",
      "--no-compile-autoload-bunfig",
      "--no-compile-autoload-tsconfig",
      "--no-compile-autoload-package-json",
      `--target=bun-${platform}${process.arch === "x64" ? "-baseline" : ""}`,
      entry,
      "--outfile",
      join(output, name),
    ],
    { cwd: source, stdio: "inherit" },
  );
  const identity = execFileSync(join(output, name), ["--version"], {
    env: { PATH: "" },
    cwd: output,
    encoding: "utf8",
    timeout: 5000,
  }).trim();
  if (identity !== `${name} ${spec.version}`)
    throw new Error("Standalone bridge identity does not match its manifest");

  const notices = [
    `${label}\nUpstream wrapper: Apache-2.0\nUpstream: ${upstream}\n` +
      `Revision: ${spec.upstream_revision}\nModified by Flowfield to provide dedicated-session cleanup.\n`,
    await readFile(join(source, "LICENSE"), "utf8"),
  ];
  // Retain notices from locked dependencies (including build-only ones). Avoid a
  // handwritten dependency list that loses transitive copyright notices on upgrades.
  async function licenses(directory) {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const path = join(directory, entry.name);
      if (entry.isDirectory()) await licenses(path);
      else if (
        entry.isFile() &&
        /^(licen[cs]e|notice|copying)(\.|$)/i.test(entry.name)
      ) {
        notices.push(
          `\n--- ${path.slice(source.length + 1)} ---\n` +
            (await readFile(path, "utf8")),
        );
      }
    }
  }
  await licenses(join(source, "node_modules"));
  const bunNotice = await fetch(
    `https://raw.githubusercontent.com/oven-sh/bun/bun-v${spec.bun_version}/LICENSE.md`,
  );
  if (!bunNotice.ok)
    throw new Error("Cannot retrieve the pinned runtime's license");
  notices.push(
    `\n--- Bun ${spec.bun_version} ---\n` + (await bunNotice.text()),
  );
  await writeFile(join(output, "THIRD_PARTY_NOTICES.txt"), notices.join("\n"));
  await writeFile(
    join(output, "LICENSE"),
    await readFile(join(root, "../../LICENSE")),
  );
  const files = {};
  for (const filename of [name, "LICENSE", "THIRD_PARTY_NOTICES.txt"])
    files[filename] = createHash("sha256")
      .update(await readFile(join(output, filename)))
      .digest("hex");
  await writeFile(
    join(output, "manifest.json"),
    JSON.stringify(
      { schema: 1, name: name, ...spec, platform, files },
      null,
      2,
    ) + "\n",
  );
  const archive = resolve(
    root,
    ".work/packages",
    `${name}-${spec.version}-${platform}.zip`,
  );
  // -FS removes stale members if a previous build used a different file list.
  execFileSync(
    "zip",
    ["-q", "-X", "-FS", archive, "manifest.json", ...Object.keys(files)],
    { cwd: output },
  );
  const digest = createHash("sha256")
    .update(await readFile(archive))
    .digest("hex");
  await writeFile(
    archive + ".sha256",
    `${digest}  ${archive.split("/").pop()}\n`,
  );
  console.log(archive);
}
