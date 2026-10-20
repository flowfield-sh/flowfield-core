import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { expect } from "@playwright/test";
import { test, state } from "./support";
import { openResource } from "./shell-navigation";

test("workspace files organize retained task outputs without a project", async ({
  page,
  request,
}, testInfo) => {
  const value = JSON.parse(
    execFileSync(
      resolve("../.venv/bin/python"),
      [resolve("../tests/file_browser_fixture.py"), state],
      { encoding: "utf8" },
    ),
  );
  const base = `/workspaces/${value.workspace}`;
  await page.setViewportSize({ width: 1440, height: 1000 });
  const failedReads: string[] = [];
  page.on("response", (response) => {
    if (response.url().includes("/api/") && response.status() === 404)
      failedReads.push(response.url());
  });
  await page.goto(`${base}/tasks/${value.waiting_task}`);
  const footer = page.locator(".entity-pane-footer");
  await expect(footer).toHaveCount(0);
  await expect(
    footer.getByRole("button", { name: "Stop review", exact: true }),
  ).toHaveCount(0);
  // The scripted waiting review has no retained activity; its question owns the response.
  await expect(page.locator('[data-kind="review"]')).toBeVisible();
  await expect(page.locator('[data-kind="review"] summary')).toHaveCount(0);
  await page.goto(`${base}/requests/question/${value.waiting_question}`);
  await footer
    .getByRole("textbox", { name: "Your answer", exact: true })
    .fill("Use a 1024 pixel viewport.");
  await footer
    .getByRole("button", { name: "Send answer", exact: true })
    .click();
  await page.goto(`${base}/tasks/${value.waiting_task}`);
  await expect(page.getByLabel("Task feed")).toContainText(
    "Use a 1024 pixel viewport.",
  );
  await page.reload();
  await expect(page.getByLabel("Task feed")).toContainText(
    "Use a 1024 pixel viewport.",
  );
  expect(failedReads).toEqual([]);
  await page.goto(`${base}/files`);
  const files = page.getByRole("region", { name: "Files", exact: true });
  const taskFilter = files.getByRole("combobox", {
    name: "Filter files by task",
  });
  await taskFilter.click();
  await page
    .getByRole("option", { name: new RegExp(`^${value.task} ·`) })
    .click();
  await expect(
    files.getByText("launch-report.txt", { exact: true }),
  ).toBeVisible();
  await expect(
    files.getByText("checklist-draft.txt", { exact: true }),
  ).toHaveCount(0);
  await taskFilter.click();
  await page.getByRole("option", { name: "All files", exact: true }).click();
  await files.getByRole("button", { name: "evidence", exact: true }).click();
  const firstPage = await (
    await request.get(`/api${base}/files/browse?path=evidence&sort=newest`)
  ).json();
  expect(firstPage.next_offset).not.toBeNull();
  expect(firstPage.items.length).toBeLessThan(55);
  await expect(
    files.getByRole("list", { name: "Folder contents" }).locator("li"),
  ).toHaveCount(firstPage.items.length);
  await files
    .getByRole("button", { name: "Load more files", exact: true })
    .click();
  await expect(
    files.getByRole("list", { name: "Folder contents" }).locator("li"),
  ).toHaveCount(55);
  const contents = files.getByRole("list", { name: "Folder contents" });
  const lastFile = contents.getByRole("button").last();
  await contents.hover();
  await page.mouse.wheel(0, 10000);
  await expect(lastFile).toBeInViewport();
  await lastFile.click();
  await expect(
    files
      .getByRole("region", { name: "File details", exact: true })
      .getByRole("heading"),
  ).toBeInViewport();
  await expect(taskFilter).toBeInViewport();
  await expect(
    files.getByRole("combobox", { name: "Sort files" }),
  ).toBeInViewport();
  await lastFile.hover();
  await page.mouse.wheel(0, -10000);
  await expect(contents.getByRole("button").first()).toBeInViewport();
  await files
    .getByRole("navigation", { name: "File location" })
    .getByRole("button", { name: "All files", exact: true })
    .click();
  await expect(
    files.getByText("launch-report.txt", { exact: true }),
  ).toBeVisible();
  await expect(
    files.getByText("checklist-draft.txt", { exact: true }),
  ).toBeVisible();
  await files.getByRole("button", { name: /launch-report.txt/ }).click();
  await expect(files.getByLabel("Preview of launch-report.txt")).toContainText(
    "The pilot supports a focused launch.",
  );
  const details = files.getByRole("region", {
    name: "File details",
    exact: true,
  });
  await expect(
    details.getByRole("link", { name: "Source result", exact: true }),
  ).toHaveAttribute("href", `${base}/results/${value.result}`);
  expect(await (await request.get(`/api${base}/projects`)).json()).toEqual([]);
  const download = page.waitForEvent("download");
  await details.getByRole("link", { name: "Download", exact: true }).click();
  expect((await download).suggestedFilename()).toBe("launch-report.txt");
  await files.getByRole("button", { name: /preview.js/ }).click();
  const code = files.getByLabel("Preview of preview.js");
  await expect(code).toContainText("<img src=x onerror=alert(1)>");
  await expect(code.locator(".token.keyword").first()).toHaveText("const");
  await expect(code.locator("img,script")).toHaveCount(0);
  await files.getByRole("button", { name: /preview.md/ }).click();
  const markdown = files.getByLabel("Preview of preview.md");
  await expect(
    markdown.getByRole("heading", { name: "Launch notes" }),
  ).toBeVisible();
  await expect(markdown.locator("script")).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath("markdown-preview.png") });
  await files.getByRole("button", { name: /sources.csv/ }).click();
  const csv = files.getByRole("table", { name: "CSV preview" });
  await expect(
    csv.getByRole("cell", { name: "Interviews, users", exact: true }),
  ).toBeVisible();
  await expect(
    csv.getByRole("cell", { name: "0012", exact: true }),
  ).toBeVisible();
  await expect(
    csv.getByRole("cell", { name: 'Said "yes"', exact: true }),
  ).toBeVisible();
  await expect(
    csv.getByRole("cell", { name: "=1+1", exact: true }),
  ).toBeVisible();
  await expect(csv.locator("img,script")).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath("csv-preview.png") });
  await files.getByRole("button", { name: /large.csv/ }).click();
  await expect(csv.getByRole("row")).toHaveCount(201);
  await expect(files.getByText(/Table preview is limited/)).toBeVisible();
  await files.getByRole("button", { name: /malformed.csv/ }).click();
  await expect(files.getByText(/CSV could not be displayed/)).toBeVisible();
  await files.getByRole("button", { name: /long.txt/ }).click();
  const long = files.getByLabel("Preview of long.txt");
  await expect(long).toContainText("Long retained text");
  await expect
    .poll(async () => {
      const preview = (await long.boundingBox())!;
      const inspector = (await files
        .getByRole("complementary", { name: "Selected file" })
        .boundingBox())!;
      return Math.abs(
        preview.y + preview.height - inspector.y - inspector.height,
      );
    })
    .toBeLessThan(3);
  await page.screenshot({ path: testInfo.outputPath("text-full-height.png") });
  await files.getByRole("button", { name: /launch-report.txt/ }).click();
  await expect
    .poll(
      async () =>
        (await files.getByLabel("Preview of launch-report.txt").boundingBox())
          ?.height ?? 1000,
    )
    .toBeLessThan(150);
  for (const [width, height, theme] of [
    [1440, 1000, "light"],
    [1440, 1000, "dark"],
    [390, 844, "light"],
    [390, 844, "dark"],
  ] as const) {
    await page.setViewportSize({ width, height });
    await page.emulateMedia({ colorScheme: theme });
    if (theme === "dark")
      await expect(page.locator("html")).toHaveClass(/\bdark\b/);
    else await expect(page.locator("html")).not.toHaveClass(/\bdark\b/);
    await page.screenshot({
      path: testInfo.outputPath(`files-${width}-${theme}.png`),
      animations: "disabled",
    });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.emulateMedia({ colorScheme: "light" });
  await details
    .getByRole("link", { name: "Source result", exact: true })
    .click();
  await expect(page).toHaveURL(`${base}/results/${value.result}`);
  await openResource(page, "Files");
  await details.getByRole("link", { name: "T-1", exact: true }).click();
  await expect(page).toHaveURL(`${base}/tasks/${value.task}`);
  await openResource(page, "Files");
  await expect(
    details.getByRole("heading", { name: "launch-report.txt", exact: true }),
  ).toBeVisible();
  await page.goto(`${base}/results/${value.result}`);
  await expect(
    page.getByText("launch-report.txt", { exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("file-task-completed.png"),
  });
  await page.goto(`${base}/results/${value.partial_result}`);
  await expect(
    page.getByText("checklist-draft.txt", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Request follow-up", exact: true }),
  ).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("file-task-partial.png") });
  await openResource(page, "Files");
  await expect(
    files.getByRole("button", { name: /launch-report.txt/ }),
  ).toBeVisible();
  await files.getByRole("button", { name: /checklist-draft.txt/ }).click();
  await expect(
    details.getByRole("link", { name: "Source result", exact: true }),
  ).toHaveAttribute("href", `${base}/results/${value.partial_result}`);
  await details
    .getByRole("link", { name: "Source result", exact: true })
    .click();
  await expect(page).toHaveURL(`${base}/results/${value.partial_result}`);
  await page.goto(`${base}/results/${value.review_result}`);
  await expect(
    page.getByRole("button", { name: "Retry review", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Approve and integrate", exact: true }),
  ).toHaveCount(0);
  await expect(page.getByText("assessment.txt", { exact: true })).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("file-review-stopped.png"),
  });
});
