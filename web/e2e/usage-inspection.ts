import { expect, type Page } from "@playwright/test";

/** Media changes arrive asynchronously; capture only after the actual app theme settles. */
export async function settleUsageTheme(
  page: Page,
  colorScheme: "light" | "dark",
) {
  await page.emulateMedia({ colorScheme });
  await expect(page.locator("html")).toHaveCSS("color-scheme", colorScheme);
  await page.evaluate(async () => {
    await new Promise<void>((resolve) =>
      requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
    );
    await Promise.all(
      document
        .getAnimations()
        .filter(
          (animation) => animation.effect?.getTiming().iterations !== Infinity,
        )
        .map((animation) => animation.finished.catch(() => {})),
    );
  });
}
