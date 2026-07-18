# ui-multi_select

## Purpose

Combo-box with checkboxes that binds to an **array** field. Supports static or dynamic option sources from factory configuration.

## When to use / when NOT

**Use:** Selecting multiple items from a predefined set of options; filtering or tagging workflows.

**NOT:** For single-selection (use `select` instead); for user-input strings (use `textarea` or `text_input`).

## YAML shape

```yaml
component: multi_select
config:
  field: <string>                    # array field path (e.g., tags, data.inputs)
  label: <string>                    # display label
  placeholder: <string>              # [optional] prompt text
  options: <array>                   # [static] array of {value, label} objects
  options_from: states|agents|volumes # [dynamic] pull from factory config; takes precedence over options
  exclude_self_field: <string>       # [optional] hide option matching this field value
  disabled: <bool>                   # [optional] grey out / block editing (default false)
  required: <bool>                   # [optional] mark as mandatory (default false)
```

## Config keys

| Key | Type | Required | Notes |
|-----|------|----------|-------|
| `field` | string | Yes | Path to array field; updates directly |
| `label` | string | Yes | Displayed above the component |
| `options` | array | When static | Objects with `value` and `label` keys |
| `options_from` | enum | When dynamic | `'states'`, `'agents'`, or `'volumes'` — pulls slugs/names from the current factory config; takes precedence over `options` |
| `placeholder` | string | No | Hint text when no items selected |
| `exclude_self_field` | string | No | Field path; option matching that value is hidden |
| `disabled` | boolean | No | When `true`, the control is greyed out and non-editable (default `false`) |
| `required` | boolean | No | When `true`, mark as mandatory (default `false`) |

## Data & events

Array field updates directly on selection/deselection. No explicit event hooks; changes persist to the bound field.

## Example

Static options:
```yaml
component: multi_select
config:
  field: tags
  label: Tags
  options:
    - { value: urgent, label: Urgent }
    - { value: blocked, label: Blocked }
```

Dynamic options (pulls state slugs):
```yaml
component: multi_select
config:
  field: data.inputs
  label: Input states
  placeholder: Pick states…
  options_from: states
  exclude_self_field: data.id
```

## Gotchas

- **`exclude_self_field`:** Hides the option whose value equals the referenced field's value; useful to prevent self-referential selections in state/agent workflows.
- **Array binding:** Changes write directly to the array; ensure target field is initialized as an empty array.
- **Asymmetry with `select`:** `multi_select` binds an **array** and supports dynamic `options_from` (`states`/`agents`/`volumes`); `select` binds a **scalar** and is static-only (no `options_from`). Both honour `disabled` to block editing — neither has a `read_only` display-only mode (that key lives on `text_input`/`textarea`/`code_editor`).
