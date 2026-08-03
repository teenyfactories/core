# ui-detail_list

## Purpose

Read-only label→value record displayed as a clean definition list. Replaces the `read_only: text_input` display-stack hack. **DataRef-aware**: reads from surrounding context (published `row` subject) or carries its own `data:` block for standalone use.

## When to use / when NOT

**Use:**
- Inside `detail_modal` to display the clicked row's fields
- Standalone to show latest stats/metadata (single record only)
- For clean, muted field labels with formatted values

**NOT:**
- Editable fields (use `text_input`, `select`, etc.)
- Multiple records (use `table` or `list`)

## YAML shape

**Inside a modal** (no `data:` block; reads the published subject under `data`, the uniform prefix for whatever was clicked — `ui-common` § `data`):
```yaml
component: detail_list
config:
  fields:
    - { field: data.company_name, label: Company }
    - { field: data.pipeline_value, label: Pipeline value, format: currency }
    - { field: data.classified_pct, label: Classified, format: percentage }
    - { field: data.updated_at, label: Last updated, format: relative_time }
```

**Standalone** (own `data:` block):
```yaml
component: detail_list
data: { collection: stats, state: pipeline_stats, latest: true }
config:
  empty_text: No stats yet
  fields:
    - { field: prospects_monitored, label: Prospects monitored, format: number }
    - { field: pipeline_value, label: Pipeline value, format: currency }
```

## Config keys

| Key | Type | Description |
|-----|------|-------------|
| `fields[]` | array | List of label→value pairs |
| `fields[].field` | string | Dot-notation path (e.g. `data.company_name` inside a modal, bare `pipeline_value` standalone). The legacy leaf-specific prefixes `row.` / `point.` / `node.` still resolve and are deprecated (`check_ui` warns). |
| `fields[].label` | string | Muted label; falls back to field path if omitted |
| `fields[].format` | enum | Optional: `number` \| `percentage` \| `currency` \| `duration` \| `relative_time` |
| `empty_text` | string | Copy shown when no subject/data available |

## Data & events

- **Input:** DataRef context (when inside `detail_modal`) or `data:` block (standalone)
- **Events:** None—read-only component
- **Blank fields:** Render as `—`

## Gotchas

- No input chrome (no border, no focus ring)
- Display-only; no keyboard or mouse interaction
- Requires a bound subject (`data`) or explicit `data:` block; won't render without one
