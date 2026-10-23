import { expect, type Page } from "@playwright/test";

export async function openResource(page: Page, name: string) {
  if (name === "Archive") {
    await openResource(page, "Task Board");
    await page
      .locator(".board-toolbar")
      .getByRole("link", { name, exact: true })
      .click();
    return;
  }
  const resources = page.getByRole("navigation", {
    name: "Workspace resources",
  });
  const trigger = page.getByRole("button", {
    name: "Open workspaces",
    exact: true,
  });
  await expect(resources.or(trigger)).toBeVisible();
  if (!(await resources.isVisible())) await trigger.click();
  await resources.getByRole("link", { name, exact: true }).click();
}
