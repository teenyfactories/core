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
on_item_click: { open: point_detail_modal }   # TOP-LEVEL — a SIBLING of `config:`, NOT inside it
```

> **Naming:** `on_item_click` is the universal handler for "the user clicked one of the things
> this component renders" — a point here, a row on a `table`. The scatter-only `on_point_click:`
> still fires but is **deprecated** (`check_ui` warns).
>
> **Placement:** read at the component top level (`component.on_item_click`). Nesting it under
> `config:` makes points silently unclickable; `check_ui` rejects that.

## Config keys

**Data:** `data_field` (string, default — extracts array field from latest state row if data is an object).

**Axes:** `x_field`, `y_field` (required); `x_label`, `y_label`; `x_domain`, `y_domain` (`[num|auto, ...]`, default auto); `x_reverse`, `y_reverse` (bool).

**Dots & labels:** `label_field`, `show_labels`, `label_font_size` (10), `radius` (8).

**Colour:** `color_field` (categorical); `scale: { scheme: theme_categorical }`; `color_map` (per-category hex overrides); `default_color` ('var(--primary-500)' fallback); `show_legend` (bool, default `false` — category key below the plot, see § Legend).

**Quadrants:** `show_quadrants` (bool); `quadrant_labels` (object with keys `top_right`, `top_left`, `bottom_right`, `bottom_left`).

**Tooltip:** `tooltip_template` (`$:` JSONata string) — replaces the whole hover body.

**Interaction:** top-level `on_item_click: { open: id }` (modal id; string or `$:` JSONata) — not a `config:` key. Deprecated alias: `on_point_click`.

## `show_legend:` — a category key

Off by default. `show_legend: true` renders a horizontally wrapping strip of coloured dots and labels below the plot, naming what each `color_field` category is:

```yaml
config:
  color_field: region
  color_map: { NSW: '#2563eb', VIC: '#7c3aed' }
  show_legend: true
```

- **Position is fixed** below the plot — there is no `legend_position`, `legend_title` or `legend_max_items`. The plot shrinks by the key's height (~34px for one row) and re-measures itself.
- **Overflow is deterministic, never truncated.** The strip caps at 25% of the component's height and scrolls; a 30-category plot degrades to a small scrollable key rather than hiding the very category the reader is hunting for.
- **Swatch colours come from the same resolver as the dots** (`color_map` → `scale.scheme` → `default_color`), so the key cannot drift from the plot.
- Entries are non-interactive — clicking one does not filter the plot.

## `tooltip_template:` — a custom hover body

By default the tooltip is three fixed rows: the `label_field`, then `x_label: value` and `y_label: value`. `tooltip_template` replaces all three with whatever string a JSONata expression returns, evaluated against the hovered point:

```yaml
config:
  tooltip_template: >-
    $: title & " — " & sector & "\n" &
       "Headroom: $" & $string($round(headroom_aud / 1000000, 1)) & "m\n" &
       "Strength: " & $string(strength_score) & "/100\n\n" &
       "Comment: " & (comment ? comment : "no read yet")
```

- The point's fields are addressable bare (`sector`) or under the uniform clicked-subject prefix (`data.sector`) — the same context `on_item_click` gets, so an expression can be copied between the two.
- **Newlines are honoured** (`\n`) and long lines wrap at ~320px. The default tooltip stays single-line-per-row; only a templated one wraps.
- It must start with `$:`. A plain literal is rejected by the schema — it would paint identical text on every point, which is never what was meant.
- Compute the prose in the agent that writes the row, not in the template. JSONata is for stitching fields together; a sentence that needs judgement (an LLM read on a segment, say) belongs in a field the template merely references.
- A template that throws or returns a non-string falls back to the default body and logs once — it never blanks the tooltip.

## Data & events

**Input:** `collection` or `{ collection, state, latest: true, data_field }` — array of row objects.

**Point click event:** publishes the clicked point to DataRef; descendants resolve `$: data.field` — the uniform prefix for the clicked subject on every leaf (`ui-common` § `data`). Siblings (e.g., modals) read it the same way a table-row or card click would. The scatter-specific spelling `$: point.field` still resolves and is **deprecated** (`check_ui` warns).

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
on_item_click: { open: point_detail_modal }
```

Pair with a modal registered by id `point_detail_modal` that reads `$: data.site_name` and `$: data.scores_markdown`.

## Gotchas

- **Axis ranges are independent:** `[0, auto]` pins floor, auto-fits ceiling with ~5% padding. Mixing is common.
- **No outlier clamping:** auto-fit spans full data min→max; filter upstream if a single far point widens the axis undesirably.
- **Reversed axes flip pixels only:** `x_reverse: true` moves high values left; labels and tooltips still read real ascending values. Don't use `x_domain: [100, 0]`.
- **Colour order:** `color_map` → auto-palette (`scale.scheme`) → `default_color` (null fallback). List only pinned categories in `color_map`.
- **Quadrants + reversed axes:** cross stays at domain midpoint; labels name visual corners, so they land correctly after a flip.
- **The legend lists categories PRESENT IN THE DATA, not every `color_map` entry.** Pin eight regions in `color_map` but plot only three and the key shows three — it never advertises a class with no dots. Points whose `color_field` value is missing or null get one trailing **`Unspecified`** entry (in `default_color`), always last, and only when such points exist. `show_legend: true` renders nothing at all when there is no `color_field`, or when fewer than two classes (counting `Unspecified`) are on screen — a one-colour key is noise.
