# ui-text_input · Purpose · When to use / when NOT · YAML shape · Config keys · Data & events · Example · Gotchas

## Purpose

Single-line text input field for forms. Supports text, email, password, and numeric types. Renders with label, placeholder hint, optional validation (required), and read-only display mode.

## When to use / when NOT

**Use:** Single-line text, email, password, or numeric inputs.

**NOT:** Long-form text (→ `textarea`), multi-select (→ `multi_select`), code (→ `code_editor`).

## YAML shape

```yaml
component: text_input
config:
  field: data.name                    # required — dot-path to bound field
  label: Name                         # optional — field label
  placeholder: Enter name             # optional — greyed hint shown when empty
  type: text                          # text | email | password | number (default: text)
  required: true                      # optional bool — form validation
  read_only: false                    # optional bool — display-only mode
```

## Config keys

| Key | Type | Meaning |
|---|---|---|
| `field` | string (dot-path) | **Required.** Dot-path to the bound field in the current DataRef. Used for both read and write. |
| `label` | string | Display label above the input. |
| `placeholder` | string | Greyed placeholder text shown inside empty input. |
| `type` | enum | Input type: `text` (default), `email`, `password`, `number`. HTML input semantics apply. |
| `required` | bool | If true, empty field blocks form submission and shows validation error. |
| `read_only` | bool | If true, field is display-only; `format:` enum applies (see Data & events). |
| `hint` | string | Optional help text shown via `?` info icon beside the label. |
| `format` | string | Display format for read-only mode (e.g., `"relative_time"` for timestamps). |

## Data & events

**Binding:** Reads and writes via `field` from the current DataRef scope.

**Events:** Supports `on_change` and `on_blur` (see `ui-common` for event semantics).

**Read-only mode:** When `read_only: true`, field is display-only with optional `format:` (e.g., `"relative_time"` for timestamps).

## Example

```yaml
- component: layout_row
  children:
    - component: text_input
      config:
        field: data.email
        label: Email
        placeholder: user@example.com
        type: email
        required: true
      on_blur:
        action: save_data_item
        collection: contacts
        key: "$: contact_id"

    - component: text_input
      config:
        field: data.count
        label: Quantity
        type: number
        placeholder: "0"
```

## Gotchas

- **Bare field names:** `field:` paths are resolved against the current DataRef scope. In modals or nested contexts, use full dot-paths (e.g., `data.invoice_id` not just `invoice_id`).
- **HTML type semantics:** `type: email` enforces basic email validation; `type: number` accepts numeric input only. The `required` flag adds form validation on top.
- **Read-only format:** `read_only: true` + `format: "relative_time"` shows a timestamp as "5m ago"; `format` is ignored for writable fields.
- **No multi-line:** `text_input` is single-line only. For multi-line input, use `textarea`.
