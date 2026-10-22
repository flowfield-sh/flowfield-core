import { expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { test, state, checkout, projectWorkspace, connectMcp } from "./support";
import { dialogViews } from "./dialog-evidence";

test("candidate checks stream real output to the result and Coordinator without losing reading position", async ({
  page,
  request,
}, testInfo) => {
  test.setTimeout(60_000);
  const seed = JSON.parse(
    execFileSync(
      join(checkout, ".venv/bin/python"),
      [
        join(checkout, "scripts/create-integration-demo.py"),
        join(state, "progress-fixture"),
        "--state",
        state,
        "--project-id",
        "validation-progress",
      ],
      { encoding: "utf8" },
    ),
  );
  const workspace = await projectWorkspace(request, seed.project);
  const base = `/api/workspaces/${workspace}`;
  const settings = await (
    await request.get(`/api/projects/${seed.project}/integration`)
  ).json();
  const gate = join(state, "validation-progress-release");
  const command = `i=0; while [ "$i" -lt 100 ]; do echo "Observed output $i"; i=$((i+1)); done; while [ ! -f '${gate.replaceAll("'", "'\\''")}' ]; do sleep 0.1; done; echo 'Check complete'`;
  const configured = await request.put(
    `/api/projects/${seed.project}/integration`,
    {
      data: {
        expected_revision: settings.revision,
        target_branch: settings.target_branch,
        checks: [command],
        check_timeout_seconds: 45,
      },
    },
  );
  expect(configured.ok()).toBeTruthy();
  const old = await (
    await request.get(`${base}/results/${seed.result}`)
  ).json();
  const response = await request.post(`${base}/results/${old.id}/prepare`, {
    data: { expected_revision: old.revision, author: "human" },
  });
  expect(response.ok()).toBeTruthy();
  const result = await response.json();
  try {
    await page.goto(`/workspaces/${workspace}/results/${result.id}`);
    const progress = page.getByRole("region", { name: "Validation progress" });
    await expect(progress).toContainText("Running project checks · 1 of 1");
    await expect(progress).toContainText("Observed output 99");
    await expect(progress).toContainText("45s limit");
    const output = progress.locator("pre");
    await output.evaluate((element) => {
      element.scrollTop = 30;
    });
    const before = await output.evaluate((element) => element.scrollTop);
    await expect(progress).toContainText(/Last output [1-9]\d*s ago/);
    expect(await output.evaluate((element) => element.scrollTop)).toBe(before);
    const latest = await (
      await request.get(`${base}/results/${result.id}`)
    ).json();
    const mcp = await connectMcp(request);
    const evidence = await mcp("get_integration", {
      workspace_id: workspace,
      integration_id: latest.integration_id,
    });
    expect(evidence.progress.current.output).toContain("Observed output 99");
    await dialogViews(page, testInfo, "validation");
  } finally {
    writeFileSync(gate, "finish");
  }
  await expect(
    page.getByRole("region", { name: "Validation progress" }),
  ).toHaveCount(0);
  await expect(
    page.getByText("Flowfield: 1 of 1 project checks passed"),
  ).toBeVisible();
  await page.goto(`/projects/${seed.project}/edit/integration`);
  await page.getByLabel("Validation commands", { exact: true }).fill("");
  await page
    .getByRole("button", { name: "Save integration settings", exact: true })
    .click();
  await expect
    .poll(
      async () =>
        (
          await (
            await request.get(`/api/projects/${seed.project}/integration`)
          ).json()
        ).checks,
    )
    .toEqual([]);
  await page.reload();
  await expect(
    page.getByLabel("Validation commands", { exact: true }),
  ).toHaveValue("");
});
