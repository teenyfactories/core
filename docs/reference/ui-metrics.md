# ui-metrics

## Purpose
Display a set of labeled KPIs or summary statistics in a grid layout, formatted as numbers, percentages, currency, or durations. Each metric fetches its value from a single field in the bound `data:` collection/state.

## When to use / when NOT
**Use:** dashboard headers, summary panels, KPI dashboards.
**NOT:** to display a clicked subject's fields (use `detail_list` instead); to read a published subject's state (metrics reads only its own `data:` block).

## YAML shape
```yaml
component: metrics
title: (string)
data:
  collection: (string)
  state: (string)
  latest: (boolean)
config:
  direction: (horizontal | vertical)
  metrics:
    - field: (string)
      label: (string)
      icon: (string, optional)
      format: (number | percentage | currency | duration | relative_time)
      style: (optional)
        color: (CSS string or $: expression)
```

## Config keys
- **field** — name of the field in the data record to display.
- **label** — human-readable metric label.
- **icon** — optional icon slug (e.g. `users`, `dollar-sign`).
- **format** — how to render the value:
  - `number`: non-compact, full precision (via `Intl.NumberFormat('en-AU')`, e.g. `1,234`).
  - `currency`: compact AUD (e.g. `$4.5M`, `$1.2K`, `$950`). AUD and en-AU are hardcoded; no per-metric locale.
  - `percentage`, `duration`, `relative_time`: as expected.
- **style** — optional CSS overrides; `color` can be a CSS string or a `$:` conditional expression.

## Data & events
Reads from `data.collection` / `data.state` with `latest: true` flag. No events; read-only.

## Example
```yaml
component: metrics
title: Pipeline Summary
data:
  collection: stats
  state: pipeline_stats
  latest: true
config:
  direction: horizontal
  metrics:
    - field: prospects_monitored
      label: Prospects Monitored
      icon: users
      format: number
    - field: pipeline_value
      label: Pipeline Value
      icon: dollar-sign
      format: currency
    - field: classified_pct
      label: Classified
      format: percentage
      style:
        color: "$:classified_pct >= 80 ? 'var(--success-500)' : 'var(--warning-500)'"
```

## Gotchas
- `metrics` reads **only** its own `data:` block; it cannot see a published subject. To display a subject's fields, use `detail_list`.
- `currency` is always AUD (hardcoded); there is no currency or locale customization per metric.
