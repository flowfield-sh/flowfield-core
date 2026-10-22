import { expect, type Page, type TestInfo } from "@playwright/test";

// Capture complete views with the real theme owner; no component or style overrides.
export async function dialogViews(page: Page, info: TestInfo, name: string) {
  const originalViewport = page.viewportSize()!;
  const hasDialog = (await page.getByRole("alertdialog").count()) > 0;
  await page.evaluate(() => {
    localStorage.setItem("flowfield.theme", "system");
    window.dispatchEvent(
      new StorageEvent("storage", { key: "flowfield.theme" }),
    );
  });
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
    for (const colorScheme of ["light", "dark"] as const) {
      await page.emulateMedia({ colorScheme, reducedMotion: "reduce" });
      await expect(page.locator("html")).toHaveCSS("color-scheme", colorScheme);
      const dialog = page.getByRole("alertdialog");
      if (hasDialog) {
        await expect(dialog).toBeInViewport();
        for (const button of await dialog.getByRole("button").all()) {
          await expect(button).toBeInViewport();
          // A bounding box alone misses a popover painted above the modal.
          await button.click({ trial: true });
        }
        const bounds = (await dialog.boundingBox())!;
        expect(bounds.x).toBeGreaterThanOrEqual(0);
        expect(bounds.x + bounds.width).toBeLessThanOrEqual(width);
      }
      await page.screenshot({
        path: info.outputPath(`${name}-${width}-${colorScheme}.png`),
        animations: "disabled",
        fullPage: true,
      });
    }
  }
  await page.setViewportSize(originalViewport);
}
