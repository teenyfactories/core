# ui-card

## Purpose

A contained panel with an optional title, an optional right-aligned header control strip, and a body region. The body is a **region** that infers its layout axis from child types and owns padding, scroll, and gap.

## When to use / when NOT

**Use** for framed content panels inside dashboards, layouts, and multi-view interfaces. **NOT** for top-level page shells (use layout primitives) or single-column document flows without padding. Cards nest well in `layout_row` / `layout_column` for responsive dashboards.

## YAML shape

```yaml
component: card
title: Card Title
config: { flex: 1 }
header_buttons:
  - component: button
    config: { label: Add person, icon: user-plus, size: small, variant: secondary }
    on_click: { action: open, id: new_person_modal }
children:
  - component: table
    data: { collection: people }
```

## Config keys

- **flex**: `0` (content-sized, default) or `N > 0` (fills share of parent space and cross axis). Set `flex: 1` for cards that should fill a parent container.
- **gap**: overrides the token gap between body children.

Top-level (not under `config:`):

- **title**: header text.
- **header_buttons**: array of component configs rendered right-aligned on the title's own baseline.

## `header_buttons:` — controls level with the title

The recurring shape is "action buttons above a table or a kanban". Written as a body child, the buttons land a row *below* the title and read as adrift from it:

```yaml
# DON'T — the button sits under the title, not beside it
children:
  - component: layout_row
    config: { justify: flex-end }
    children: [ { component: button, config: { label: Add person } } ]
  - component: table
```

```yaml
# DO
header_buttons:
  - component: button
    config: { label: Add person }
children:
  - component: table
```

Notes:

- Entries are **ordinary component configs**, not a button-specific shape. A `select` filter, a `button_group`, or a `tag_list` legend is equally valid — it is a generic header region that happens to hold buttons most of the time.
- They render right-aligned whether or not the card has a `title:`.
- On a card that also carries `on_click:`, clicking a header button fires only that button — the card's own handler is suppressed for clicks on interactive leaves.
- Header entries are not a region: they lay out on one row by definition, so the `no-mixed-axis` rule does not apply to them.
- With no `header_buttons:` the card renders exactly as it always has.

## Data & events

**`on_click`** (top-level, like every other handler) makes the whole card a clickable tile — the canonical way a kanban card, a dashboard tile or a record summary opens a modal or writes a row:

```yaml
- component: card
  on_click:
    action: open
    id: opportunity_modal
    subject:
      slug: '$: data._key'      # bare `$: _key` works too — see below
      title: '$: data.title'
  children:
    - component: detail_list
      config: { fields: [{ field: title, label: Deal }] }
```

Params are flat siblings of `action:`; an array fires several specs in order. Without `on_click:` the card is inert and renders exactly as before. A clickable card gets `role="button"`, `tabIndex=0`, Enter/Space activation, a pointer cursor and a hover/focus outline.

A click that lands on an interactive leaf inside the card (`button`, `a`, `input`, `textarea`, `select`, anything `role="button"`) belongs to that leaf — the card handler does not also fire. So a card can carry both a whole-tile click and its own action buttons.

Inside a `kanban`, the card's DataRef is the board row, so `$: _key`, `$: _state` and every field resolve as expected. `$: data._key` resolves to the same thing — `data` is the uniform name for "the thing that was clicked" across every leaf that fires a handler (table row, card, scatter point, force-graph node), so a subject written with `data.` reads identically wherever it's copied. Never `$: row.…` on a card: `row` is the table's own wrapper name and is undefined here (and deprecated there — `ui-common` § `data`).

No native data binding on the card itself. The card body is a region; children may carry their own `data:` blocks and event handlers. The region infers its axis:
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
      header_buttons:
        - component: button
          config: { label: Export, variant: secondary, size: small }
          on_click: { action: custom:export }
      children:
        - component: table
          data: { collection: records }
```

## Gotchas

- **`slot:` does nothing on a card.** `slot: header` / `slot: footer` on a card child is accepted by the validator but ignored by the renderer — every child renders in the body, in source order. Use `header_buttons:` for header controls; there is no footer region. (Card is listed in the renderer's slot allowlist but never consumed the grouping — `[composable:card-slots-advertised-not-implemented]`.)
- **Region axis inference** — see [Layout & responsive](ui-common.md) for full rules. A card body mixing `layout_row` + `layout_column` siblings is invalid (`no-mixed-axis`).
- **flex goes on config** — use `config: { flex: N }` to fill space, never `style: { flex: ... }`.
- **Padding is automatic** — the region applies `--spacing-panel` inset padding; do not wrap content in extra spacing layers.
- **Scroll is automatic** — overflow: auto on multi-child body; `hidden` on single self-scrolling leaves (table, chart).
