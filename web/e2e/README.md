# Browser verification

Run `make smoke` from the repository root after `make setup` and `pnpm --dir web exec playwright install chromium`.
Playwright uses `server.mjs` to start an owned service with a temporary store, check readiness, and shut it down before removing the store.
Set `FLOWFIELD_SMOKE_PORT` to an unused local port when another checkout is running the suite; the browser, service and fixture clients share that port (default `8766`).
Set `FLOWFIELD_SMOKE_STATE` only when intentionally retaining an isolated fixture store for inspection; do not use a live user store.

For the Inbox scrolling regression, build with `pnpm --dir web build`, then run `pnpm --dir web exec playwright test e2e/inbox-scroll.spec.ts --project=journeys`.
The fixture combines a real code result with a question, expands response forms and evidence, and measures document/pane geometry while using wheel, keyboard and Chromium touch input.
It also checks drafts, scroll restoration, resizing, compact surface switching, stale/pending responses, empty/single/multiple requests, and Delivered/History.
Screenshots are written to Playwright's test output directory for visual inspection; failures retain traces.
The shared layout still requires the full smoke suite, including Board's independent horizontal/column scrolling and the existing navigation and response journeys.
