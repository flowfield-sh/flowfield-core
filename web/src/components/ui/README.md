# Component provenance

Generated with the official shadcn CLI/registry on 2026-09-23, using the `new-york`
Radix components and neutral CSS variables. Configuration: `web/components.json`.
Source: https://github.com/shadcn-ui/ui (MIT; see LICENSE.md).

Keep standard component variants and update through the registry. Local adaptations:
sidebar skeleton width is deterministic rather than random; the generated mobile hook
uses useSyncExternalStore to meet this repository's React purity rules; badges use the
shared compact rounded radius instead of the registry's pill default. Outline buttons explicitly
use foreground text so linked actions and buttons inside muted containers match ordinary buttons. Formatting follows
the repository. The application uses the icon-collapsible Sidebar and its mobile
sheet. Hidden tooltip content is omitted so it cannot intercept the pointer grace
area of the next visible tooltip.

Application-specific layout, colors for task types, router integration, and retained
editor drafts live outside this directory. The `cn` utility package is the official
registry's current generated import. Do not fork controls independently per view.

Resizable was added from the official `new-york-v4` registry on 2026-10-04,
using react-resizable-panels 4.14.2 (MIT). It uses the current Group/Separator API;
the legacy new-york wrapper targets an older library API.

Sonner 2.0.8 (MIT) was added on 2026-10-10, following the official shadcn Sonner integration. The shared wrapper uses neutral theme tokens and Sonner’s semantic colors, timing, dismissal and accessible announcements. A stable portal moves its single viewport into the current Radix modal focus scope so keyboard and pointer dismissal work without losing active notifications.
