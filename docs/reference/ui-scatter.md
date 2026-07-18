# ui-scatter

## Purpose

2D X/Y scatter plot with axes, optional quadrant crosshairs, colour-coded dots by category, hover tooltips, and click-to-open-modal interaction.

## When to use / when NOT

**Use** when: comparing two numeric variables (e.g., people_score vs outcomes_score) to reveal clusters, outliers, or correlation patterns.

**Don't use** when: you have one dimension (use `bar_chart` / `line_chart`), or your data is non-numeric (use categorical charts).

## YAML shape

```yaml
component: scatter
data:
  collection: site_scores
config:
  x_field: people_score
  y_field: outcomes_score
  label_field: site_name
  color_field: tier
  on_point_click: { open: point_detail_modal }
```

## Config keys

**Axes:** `x_field`, `y_field` (required); `x_label`, `y_label`; `x_domain`, `y_domain` (`[num|auto, ...]`, default auto); `x_reverse`, `y_reverse` (bool).

**Dots & labels:** `label_field`, `show_labels`, `label_font_size` (10), `radius` (9).

**Colour:** `color_field` (categorical); `scale: { scheme: theme_categorical }`; `color_map` (per-category hex overrides); `default_color` (#a1a1aa fallback).

**Quadrants:** `show_quadrants` (bool); `quadrant_labels` (object with keys `top_right`, `top_left`, `bottom_right`, `bottom_left`).

**Interaction:** `on_point_click: { open: id }` (modal id; string or `$:` JSONata).

## Data & events

**Input:** `collection` or `{ collection, state, latest: true, data_field }` — array of row objects.

**Point click event:** publishes `{point}` to DataRef; descendants resolve `$:point.field`. Siblings (e.g., modals) can read the clicked point's data.

## Example

```yaml
component: scatter
data: { collection: site_scores }
config:
  x_field: people_score
  y_field: outcomes_score
  x_label: People
  y_label: Outcomes
  x_domain: [0, 100]
  y_domain: [0, 100]
  label_field: site_name
  color_field: tier
  color_map: { star: '#10b981', troubled: '#ef4444' }
  show_quadrants: true
  quadrant_labels:
    top_right: Stars
    top_left: Outcomes-heavy
    bottom_right: People-heavy
    bottom_left: Needs attention
  on_point_click: { open: point_detail_modal }
```

Pair with a modal registered by id `point_detail_modal` that reads `$:point.site_name` and `$:point.scores_markdown`.

## Gotchas

- **Axis ranges are independent:** `[0, auto]` pins floor, auto-fits ceiling with ~5% padding. Mixing is common.
- **No outlier clamping:** auto-fit spans full data min→max; filter upstream if a single far point widens the axis undesirably.
- **Reversed axes flip pixels only:** `x_reverse: true` moves high values left; labels and tooltips still read real ascending values. Don't use `x_domain: [100, 0]`.
- **Colour order:** `color_map` → auto-palette (`scale.scheme`) → `default_color` (null fallback). List only pinned categories in `color_map`.
- **Quadrants + reversed axes:** cross stays at domain midpoint; labels name visual corners, so they land correctly after a flip.
