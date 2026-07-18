# ui-line_chart

## Purpose

Render time-series data as line, area, or multi-series charts via Recharts. Supports grouped series by category, custom axis labels, and data-driven titles.

## When to use / when NOT

**Use:** historical trends, metrics over time, multi-line comparisons (daily spend by category, event counts over days).  
**Not:** single-value metrics (use `gauge` / `metric`), hierarchical data (use `treemap`).

## YAML shape

```yaml
component: line_chart
data:
  collection: <collection_name>
  state: <state_name>
  latest: true
config:
  title: "$: <field>"           # optional; $ resolves against chart row
  data_field: <array_field>     # field in row.value containing chart data
  x_field: <field>
  y_field: <field>
  series_field: <field>         # optional; groups into multiple lines
  x_label: <label>
  y_label: <label>
```

Alternative via `group_by`:
```yaml
component: line_chart
data:
  collection: <collection_name>
config:
  group_by: day
  days: <count>
  x_field: day
  y_field: count
```

## Config keys

| Key | Type | Required | Notes |
|-----|------|----------|-------|
| `title` | string / `$: field` | No | Heading above chart. Use `$:` to resolve field from chart row. |
| `data_field` | string | Yes | Field within `row.value` holding the array of chart points. |
| `x_field` | string | Yes | Field name for x-axis values. |
| `y_field` | string | Yes | Field name for y-axis values. |
| `series_field` | string | No | Field to group into separate lines. |
| `x_label` | string | No | X-axis label. |
| `y_label` | string | No | Y-axis label. |
| `group_by` | string | No | Aggregate mode (`day`). |
| `days` | number | No | Number of days to retrieve in `group_by` mode. |

## Data & events

Reads from `data.collection` at `data.state`. With `latest: true`, fetches the single latest row; rows must contain either `data_field` (array) or raw aggregates for `group_by` mode. No events emitted; chart is read-only.

## Example

```yaml
component: line_chart
data:
  collection: stats
  state: chart_data
  latest: true
config:
  title: "$: active_chart_label"
  data_field: line_chart
  x_field: day
  y_field: amount
  series_field: category
  x_label: Date
  y_label: "Spend ($)"
```

## Gotchas

- Title `$: field` resolves against the chart row only; use plain strings for static text.
- `series_field` requires data to contain distinct values; too many groups may render illegibly.
- In `group_by` mode, ignore `data_field` and provide aggregated rows directly from the collection.
