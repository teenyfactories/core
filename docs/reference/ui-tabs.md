# ui-tabs

## Purpose
Tabbed interface with slot-paired markers and panel containers. URL-persisted at page level; transient (non-persisted) inside modals/drawers.

## When to use / when NOT
**Use** for multi-view layouts where only one panel is visible at a time and switching preserves scroll/selection across the page session. **NOT** for toolbar buttons, dropdown toggles, or accordion-like disclosure (use component hierarchy or state flags). **NOT** for horizontal navigation (use the page routing layer).

## YAML shape
```yaml
component: tabs
config:
  default_tab: 0
children:
  - component: tab
    slot: tab
    title: Overview
    config: { icon: chart-pie }
  - component: layout_column
    slot: panel
    children: [...]
  - component: tab
    slot: tab
    title: Detail
    config: { icon: list }
  - component: card
    slot: panel
    children: [...]
```
**Slot pairing contract:** Every child declares `slot: tab` (marker leaf) or `slot: panel` (content). Tabs and panels pair by index — N tab markers **must** equal N panel containers.

## Config keys
- `default_tab: <int>` — zero-indexed active tab on load (default 0).
- `id: <string>` — optional; required if multiple Tabs on same page (URL param collision prevention).

## Data & events
No data binding. `title` on `tab` markers is required and becomes the tablist label. Panel containers hold any registered component (`layout_column`, `card`, `markdown`, etc.). Child `config.icon` is ignored (removed by product decision; retained for back-compat).

## Example
```yaml
component: tabs
config:
  default_tab: 0
  id: report_views
children:
  - component: tab
    slot: tab
    title: Summary
    config: { icon: chart-pie }
  - component: card
    slot: panel
    children:
      - component: markdown
        config: { text: "# Overview\nKey metrics here." }
  - component: tab
    slot: tab
    title: Details
  - component: layout_column
    slot: panel
    children: [...]
```

## Gotchas
- **No `config.tabs:` array form.** Only interleaved slot-paired `children` are valid; `check_ui` rejects `config.tabs`.
- **Slot contract is enforced** — `check_ui` (backend) and `types/Tabs.js` (render-time) both validate pairing. Unequal counts or missing `slot:` values cause rejection.
- **URL persistence is automatic at page level** — active tab index is mirrored to search param (key = `id:` or `tab`). Refresh/bookmark preserves state.
- **Inside transient hosts (modal/drawer), persistence is suppressed** — context automatically disables URL sync. Each open resets to `default_tab`. Tabs inside a modal are the canonical **rich record view** (overview · edit form · related records) — for a full worked table→tabbed-modal example see `read_docs{ doc: "ui-table" }` § CRUD.
- **Nested Tabs must have unique `id:`** to avoid URL param collisions.
