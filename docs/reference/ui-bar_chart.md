# ui-bar_chart

## Purpose
Categorical bar chart via Recharts. Displays data grouped by category with optional multi-series support; each series becomes a bar within each x-axis group.

## When to use / when NOT
**Use** for comparing categorical data, especially with multiple series that need grouped or stacked layout. **Avoid** for continuous numerical distributions or time-series with dense points — use `line_chart` instead.

## YAML shape
Multi-series form (pivot on `series_field`):
```yaml
component: bar_chart
data:
  collection: ideas_tally
  state: latest
  key: current
  latest: true
config:
  data_field: tally       # array field on row.data
  x_field: name           # x-axis category
  y_field: count          # bar height
  series_field: model     # one bar per unique value, grouped per x
  stacked: false          # false (default, grouped) or true (stacked)
  x_label: Idea
  y_label: Count
```

Single-series form (omit `series_field`):
```yaml
component: bar_chart
data:
  collection: events_tally
  latest: true
config:
  data_field: tally       # array field on row.data
  x_field: source         # x-axis category
  y_field: count          # bar height
```

## Config keys
| Key | Type | Purpose |
|---|---|---|
| `data_field` | string | Field on row.data containing array to iterate |
| `x_field` | string | Category field for x-axis grouping |
| `y_field` | string | Numeric field for bar height |
| `series_field` | string | Field defining series (optional; omit for single series) |
| `stacked` | boolean | `false` (default, grouped) or `true` (stacked layout) |
| `x_label`, `y_label` | string | Axis labels |
| `scale.scheme` | string | Color palette (default: `theme_categorical`); do NOT hardcode bar colours |

## Data & events
Data flows from `factory_data` row, keyed by collection/state/key. Bar colours are derived from `scale.scheme` and optional `color_map` (factory-owned, resolved via scale resolver).

## Example
**Multi-series, grouped:**
```yaml
component: bar_chart
config:
  data_field: tally
  x_field: name
  y_field: count
  series_field: model
  stacked: false
```

**Single-series:**
```yaml
component: bar_chart
config:
  data_field: tally
  x_field: source
  y_field: count
```

## Gotchas
**Tooltip filtering:** Hover tooltips list one row per series, but series with zero or null/missing value at the hovered x are hidden automatically. If all series are zero/empty at a given x, no tooltip renders. This is unconditional — no config key toggles it.

**Bar colours:** Never hardcode in factory.yml. Use `scale.scheme` (default `theme_categorical`); pass `color_map` for fixed mappings through the scale resolver.
