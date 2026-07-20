# ui-treemap

## Purpose

Hierarchical treemap visualization via Recharts. Displays nested rectangles sized and coloured by value, with tooltips showing configured fields.

## When to use / when NOT

**Use** when showing hierarchical proportional data (e.g. portfolio allocation, resource breakdown, org chart metrics).

**Not** for time-series (use `line_chart`), categorical comparisons (use `bar_chart`), or 2D scatter relationships (use `scatter`).

## YAML shape

Binds a state row and extracts a pre-aggregated array via `data_field`.

```yaml
component: treemap
data:
  collection: stats
  state: chart_data
  latest: true
config:
  data_field: treemap          # field within row.data containing the array
  name_field: name             # each item's display label
  value_field: value           # each item's size (numeric)
  tooltip_fields: [name, value, percentage]
```

## Config keys

| Key | Required | Notes |
|---|---|---|
| `data_field` | ✓ | Field on `row.data` containing the array of objects. |
| `name_field` | ✓ | Field name for item labels. |
| `value_field` | ✓ | Numeric field; drives rectangle size. |
| `tooltip_fields` | — | Array of field names for hover tooltip (default: `[name, value]`). |
| `title` | — | Optional heading above the chart (supports `$:` expressions against the row). |

## Data & events

- Input shape: array of objects with `name` and `value` keys (or renamed via config).
- Tooltip (hover): shows fields listed in `tooltip_fields` in a popup.
- No click events (treemap is read-only visualization).
- Integrates with `scale: { scheme }` for fill colour (default `theme_categorical`).

## Example

```yaml
component: card
title: Portfolio Breakdown
children:
  - component: treemap
    data:
      collection: allocations
      state: portfolio_data
      latest: true
    config:
      data_field: treemap
      name_field: asset_class
      value_field: allocation_pct
      tooltip_fields: [asset_class, allocation_pct, allocation_amount]
      title: By allocation %
```

## Gotchas

- `value_field` must be numeric and positive; negative or zero values may render invisibly.
- `name_field` is required; items without a name render with no label.
- Multi-level hierarchy (parent–child nesting) is not yet supported; only flat arrays of rectangles.
