# ui-select

## Purpose

Single-selection dropdown input. Binds to a scalar field; emits selection changes via `on_change` event.

## When to use / when NOT

**Use** for finite, mutually-exclusive choices (status, priority, category). **NOT** for multi-select (use `multi_select`), free text (use `text_input`), or large dynamic lists without filtering.

## YAML shape

```yaml
component: select
config:
  field: status                  # Dot-path to bind; reads/writes via shared DataRef
  label: Status                  # Display label
  placeholder: Choose status     # Hint text when empty (optional)
  options:                       # Static option list (required)
    - { value: active,   label: Active }
    - { value: inactive, label: Inactive }
  read_only: false               # Disable editing (optional, default false)
  required: false                # Mark as mandatory (optional, default false)
```

## Config keys

| Key | Type | Meaning |
|---|---|---|
| `field` | string | Dot-path to the scalar field to bind (e.g. `status`, `data.tier`) |
| `label` | string | Display label for the dropdown |
| `placeholder` | string | Hint text when value is empty (optional) |
| `options` | array | List of `{ value, label }` pairs. Only static options supported |
| `read_only` | boolean | When `true`, field is display-only (default `false`) |
| `required` | boolean | When `true`, empty state is invalid (default `false`) |

## Data & events

**Data binding:** Reads and writes the current selection via the shared DataRef, keying off `field`.

**Event:** `on_change` (fired when selection changes)

- Bare dispatcher — does NOT auto-attach row snapshot
- Receives only the new `value`; existing row data is preserved (state-only path)
- Useful for pure state transitions without data mutation

```yaml
on_change:
  action: save_data_item
  collection: tickets
  key: "$: ticket_id"
  data_field: status             # Optional: patch only the `status` field
  # Without data_field, state flips but the row's existing value is unchanged
```

## Example

```yaml
- component: select
  config:
    field: priority
    label: Priority
    placeholder: Set priority
    options:
      - { value: high,   label: High }
      - { value: medium, label: Medium }
      - { value: low,    label: Low }
  on_change:
    action: save_data_item
    collection: tasks
    key: "$: task_id"
    data_field: priority
```

## Gotchas

- **Static options only:** Unlike `multi_select`, `select` does not support `options_from` (dynamic sourcing from factory config states/agents). Author all options in `options:`.
- **Bare dispatcher:** `on_change` on `select` is a bare dispatcher — it does NOT carry a full row snapshot. Use `data_field` to slice a specific field, or use an explicit `data: { ... }` map to patch multiple fields.
- **Required validation:** The `required:false` default allows empty selections; toggling to `true` adds client-side validation but does not block invalid payloads server-side.
