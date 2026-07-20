# ui-layout-primitives — layout_row / layout_column

## Purpose

The canonical layout primitives for composing responsive interfaces. Prefer these over `grid`.

## When to use / when NOT

- **layout_row**: place children side by side (horizontal axis).
- **layout_column**: stack children top-to-bottom (vertical axis).
- **Do NOT use grid** — it is LEGACY; use `layout_row` / `layout_column` instead.

## YAML shape

```yaml
# Place children side by side
component: layout_row
config: { gap: 16px, align: stretch, justify: flex-start }  # all optional
children: [...]

# Stack children top-to-bottom
component: layout_column
config: { gap: 16px }
children: [...]
```

## Config keys

- **gap**: spacing between children (CSS value, e.g., `16px`).
- **align**: cross-axis alignment (CSS `align-items`; default `stretch`).
- **justify**: main-axis alignment (CSS `justify-content`; default `flex-start`).

All keys are optional.

## Data & events

Child sizing uses `config.flex` on each child:
- `flex: 0` (default) — content-sized.
- `flex: N` (N > 0) — takes N shares of leftover space and fills the cross axis.

In `layout_row`: no-flex children split width; in `layout_column`: no-flex children size to content height.

For axis inference, flex model details, regions, and responsive behaviour, see [Layout & responsive via read_docs:ui-common](read_docs:ui-common).

## Example

```yaml
component: layout_row
config: { gap: 16px }
children:
  - component: card
    title: Left
    config: { flex: 1 }
  - component: card
    title: Right
    config: { flex: 1 }
```

## Gotchas

- **No mixed axis** in a region: a modal body, tab panel, or card body must not mix `layout_row` + `layout_column` siblings.
- **Region axis inference**: a block of `layout_column` children ⇒ horizontal (side by side); a block of `layout_row` children or leaves ⇒ vertical (stacked); a single child ⇒ fills.
- **flex goes on config, not style**: use `config: { flex: N }` on children, never `style: { flex: ... }`.

## LEGACY: grid

**Deprecated.** The `grid` component still renders for existing factories but must not be used for new authoring. Migrate old grids to `layout_row` / `layout_column`:
- `direction: horizontal` → `layout_row`
- `direction: vertical` → `layout_column`
- Child `style: { flex: N }` → `config: { flex: N }`
