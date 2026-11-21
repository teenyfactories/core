# ui-pages

## Purpose
`pages` defines route-like named factory views. The orchestrator sidebar lists them by `id`/`title` and renders the selected page's own content in the main area.

## When to use / when NOT
Use for separate factory pages that belong in sidebar navigation. Use `tabs` only for in-place view switching; pages are top-level routing, not nested layout.

## YAML shape
```yaml
component: pages
children:
  - component: page
    id: overview
    title: Overview
    children:
      - component: metrics
        data: { collection: totals }
  - component: page
    id: activity
    title: Activity
    children:
      - component: table
        data: { collection: activity }
        config:
          columns: [{ field: summary, label: Summary }]
```
**Nesting contract:** every child is a `page` node. `id`/`title` label it in the sidebar; its own `children:` are the content shown when it is selected. There is no pairing and no count invariant.

## Config keys
No config. Each page is one child node:

- `id` (required, unique) — the `?page=` value.
- `title` (optional sidebar label, defaults to `id`).
- `children` — that page's content: one normal Composable UI component tree.

`pages` must be the complete `default_ui.layout` root. Nested use is rejected with a path-specific validation error.

## Gotchas
- **Nested nodes only — there is no slot-paired form.** `{ component: page, slot: page }` + `slot: panel` children are rejected outright; put the content under the page node's own `children:`.
- **No `config.pages:` array form.** `check_ui` rejects the retired config-array shape with a migration hint.
- **Structure is enforced** — `check_ui` rejects a child that isn't a `page` node, a page without `id`, or zero pages.
- **Ids must be unique** — each one is the `?page=` query value, so duplicates are rejected.
- **A layout primitive as a page's only child fills the area** — a `layout_row`/`layout_column` stretches to the whole page; several leaf children stack vertically.
- **Selection persists in the URL** — `?page=<id>`, defaulting to the first page. A bookmark or reorder keeps landing on the same page.
- **Factories without `pages` keep the normal sidebar tree** — only a factory that declares pages swaps in its page list.
