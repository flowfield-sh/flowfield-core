// Shared cursor handling for explicit pagination and retained-window refreshes.
export type Page = {
  items: unknown[];
  next_cursor?: number | string | null;
  next_before?: number | null;
  next_offset?: number | null;
};

export const pageCursor = (page: Page) =>
  page.next_cursor ?? page.next_before ?? page.next_offset;

export async function nextPage<P extends Page>(
  url: string,
  page: P,
  read: (url: string) => Promise<P>,
): Promise<P> {
  const cursor = pageCursor(page);
  const next = await read(
    `${url}${url.includes("?") ? "&" : "?"}${page.next_offset != null ? "offset" : "before"}=${encodeURIComponent(cursor!)}`,
  );
  const following = pageCursor(next);
  if (
    following != null &&
    (String(following) === String(cursor) || next.items.length === 0)
  )
    throw new Error("Could not advance through this list. Retry loading it.");
  return next;
}

export async function pageWindow<P extends Page>(
  url: string,
  count: number,
  read: (url: string) => Promise<P>,
): Promise<P> {
  let page = await read(url);
  const visited = new Set<string>();
  while (page.items.length < count) {
    const cursor = pageCursor(page);
    if (cursor == null) break;
    if (visited.has(String(cursor)))
      throw new Error("Could not advance through this list. Retry loading it.");
    visited.add(String(cursor));
    const next = await nextPage(url, page, read);
    page = { ...next, items: [...page.items, ...next.items] };
  }
  return page;
}
