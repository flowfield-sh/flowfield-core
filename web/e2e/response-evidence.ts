import { expect, type Page, type TestInfo } from "@playwright/test";
import { dialogViews } from "./dialog-evidence";

export async function responseViews(
  page: Page,
  info: TestInfo,
  name: string,
  submit: string,
  cancel: string,
) {
  const original = page.viewportSize()!;
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    const send = page.getByRole("button", { name: submit, exact: true });
    const secondary = page.getByRole("button", { name: cancel, exact: true });
    await expect(send).toBeInViewport();
    await expect(secondary).toBeInViewport();
    const primaryBox = (await send.boundingBox())!;
    const secondaryBox = (await secondary.boundingBox())!;
    expect(primaryBox.y).toBe(secondaryBox.y);
    expect(primaryBox.x).toBeLessThan(secondaryBox.x);
    await expect(
      page
        .locator(".composer-wrap")
        .getByRole("button", { name: submit, exact: true }),
    ).toHaveCount(0);
  }
  await page.setViewportSize(original);
  await dialogViews(page, info, name);
}
