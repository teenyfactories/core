# ui-card

## Purpose

A contained panel with optional header, body region, and footer slot. The body is a **region** that infers its layout axis from child types and owns padding, scroll, and gap.

## When to use / when NOT

**Use** for framed content panels inside dashboards, layouts, and multi-view interfaces. **NOT** for top-level page shells (use layout primitives) or single-column document flows without padding. Cards are slot-aware and work well nested in `layout_row` / `layout_column` for responsive dashboards.

## YAML shape

```yaml
component: card
title: Card Title
config: { flex: 1 }
children:
  - slot: header
    component: button_group
  - slot: body
    component: markdown
  - slot: footer
    component: detail_list
```

**Slot contract:** Children optionally declare `slot: header|body|footer` (default `body`).

## Config keys

- **flex**: `0` (content-sized, default) or `N > 0` (fills share of parent space and cross axis). Set `flex: 1` for cards that should fill a parent container.
- **title**: optional top-level key (not under config) — shorthand for header text.

## Data & events

No native data binding on the card itself. The card body is a region; children may carry their own `data:` blocks and event handlers. Region axis is inferred:
- Block of `layout_column` children ⇒ horizontal (side by side)
- Block of `layout_row` children or leaf components ⇒ vertical (stacked)
- Single child ⇒ fills the region

## Example

```yaml
- component: layout_row
  children:
    - component: card
      title: Summary
      config: { flex: 1 }
      children:
        - component: metrics
          data: { collection: kpis, latest: true }
    - component: card
      title: Details
      config: { flex: 1 }
      children:
        - slot: header
          component: button
          config: { label: Export, variant: secondary }
        - slot: body
          component: table
          data: { collection: records }
```

## Gotchas

- **Region axis inference** — see [Layout & responsive via read_docs:ui-common](read_docs:ui-common) for full rules. A card body mixing `layout_row` + `layout_column` siblings is invalid (`no-mixed-axis`).
- **flex goes on config** — use `config: { flex: N }` to fill space, never `style: { flex: ... }`.
- **Slot defaults to body** — children with no explicit `slot:` go into the body region.
- **Padding is automatic** — the region applies `--spacing-panel` inset padding; do not wrap content in extra spacing layers.
- **Scroll is automatic** — overflow: auto on multi-child body; `hidden` on single self-scrolling leaves (table, chart).
