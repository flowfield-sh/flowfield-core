import { expect, type Locator } from "@playwright/test";

// Exercise real keystrokes: fill() alone misses controlled-value caret resets.
export async function editInsideText(field: Locator) {
  await field.fill("Alpha omega");
  await field.focus();
  await field.evaluate((element: HTMLInputElement | HTMLTextAreaElement) =>
    element.setSelectionRange(6, 6),
  );
  await field.pressSequentially("middle ");
  await expect(field).toHaveValue("Alpha middle omega");
  expect(
    await field.evaluate((element: HTMLInputElement) => element.selectionStart),
  ).toBe(13);
  await field.evaluate((element: HTMLInputElement | HTMLTextAreaElement) =>
    element.setSelectionRange(6, 12),
  );
  await field.pressSequentially("new");
  await expect(field).toHaveValue("Alpha new omega");
  await field.press("Backspace");
  await field.press("Backspace");
  await expect(field).toHaveValue("Alpha n omega");
  expect(
    await field.evaluate((element: HTMLInputElement) => element.selectionStart),
  ).toBe(7);
  if (await field.evaluate((element) => element.tagName === "TEXTAREA")) {
    await field.fill("Alpha omega\nLast line");
    await field.evaluate((element: HTMLTextAreaElement) =>
      element.setSelectionRange(6, 6),
    );
    await field.page().keyboard.insertText("new\nline ");
    await expect(field).toHaveValue("Alpha new\nline omega\nLast line");
    await field.press("ControlOrMeta+z");
    await expect(field).toHaveValue("Alpha omega\nLast line");
  }
}
