# ui-tabs

## Purpose
Tabbed interface where each tab carries its own content. URL-persisted at page level; transient (non-persisted) inside modals/drawers.

## When to use / when NOT
**Use** for multi-view layouts where only one tab's content is visible at a time and switching preserves scroll/selection across the page session. **NOT** for toolbar buttons, dropdown toggles, or accordion-like disclosure (use component hierarchy or state flags). **NOT** for horizontal navigation (use the page routing layer).

## YAML shape
```yaml
component: tabs
config:
  default_tab: 0
children:
  - component: tab
    title: Overview
    children:
      - component: layout_column
        children: [...]
  - component: tab
    title: Detail
    children:
      - component: card
        children: [...]
```
**Nesting contract:** every child is a `tab` node. Its `title` is the tablist label; its own `children:` are the content rendered while that tab is active. Nothing is paired by index — add or remove a tab by adding or removing one node.

## Config keys
- `default_tab: <int>` — zero-indexed active tab on load (default 0).

## Data & events
No data binding. A tab's `children` hold any registered component (`layout_column`, `card`, `markdown`, etc.). Tab labels are text-only. Existing `config.icon` values on `tab` nodes are accepted but ignored; omit them in new layouts.

## Example
```yaml
component: tabs
id: report_views
config:
  default_tab: 0
children:
  - component: tab
    title: Summary
    children:
      - component: card
        children:
          - component: metrics
            data: { collection: report, latest: true }
            config:
              metrics: [ { field: total, label: Total, format: number } ]
  - component: tab
    title: Details
    children:
      - component: layout_column
        children: [...]
```

## Gotchas
- **No `config.tabs:` array form.** Tabs are nested `children`; `check_ui` rejects `config.tabs`.
- **A layout primitive as a tab's only child fills the panel** — a `layout_row`/`layout_column` stretches to the whole panel area; several leaf children stack vertically.
- **The `tabs` component needs at least two tabs** — a single-tab strip has nothing to switch to and `check_ui` flags `redundant-singleton`. Render that tab's content directly.
- **URL persistence is automatic at page level** — active tab index is mirrored to search param, keyed by the tabs node's top-level `id:` (default key `tab`; a `config.id` is a no-op). Refresh/bookmark preserves state.
- **Inside transient hosts (modal/drawer), persistence is suppressed** — context automatically disables URL sync. Each open resets to `default_tab`. Tabs inside a modal are the canonical **rich record view** (overview · edit form · related records) — for a full worked table→tabbed-modal example see `read_docs{ doc: "ui-table" }` § CRUD.
- **Nested Tabs must have unique `id:`** to avoid URL param collisions.
- **LEGACY slot-paired shape (deprecated)** — `{ component: tab, slot: tab, title: … }` markers interleaved with `slot: panel` content nodes still render, but `check_ui` returns a deprecation warning. Migrate by moving each panel's content under its tab node's `children:`. Remove once no factory in any tenant authors the old form.
