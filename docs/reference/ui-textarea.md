# ui-textarea

## Purpose

Multi-line text input for longer-form content. Reads and writes via shared DataRef using the `field` key.

## When to use / when NOT

**Use** when you need user-editable text longer than a single line (notes, descriptions, comments). **NOT** for single-line input (use `text_input`), code (use `code_editor`), or very large structured data.

## YAML shape

```yaml
component: textarea
config:
  field: description
  label: Description
  rows: 3
  read_only: false
```

## Config keys

| Key | Type | Default | Meaning |
|---|---|---|---|
| `field` | string | — | Dot-path into DataRef (e.g. `data.notes`, `description`). Required. |
| `label` | string | — | User-visible field label. |
| `rows` | number | 3 | Initial visible row height. |
| `read_only` | boolean | false | Disable editing; display mode only. |

## Data & events

- **Reads** the named `field` from the current DataRef snapshot.
- **Writes** on `on_blur` (or explicit save): shallow-merges the edited value into the row's existing value via `save_data_item` (state-only path; no `data:` → value preserved).
- **Supports** top-level event handlers: `on_change`, `on_blur`, `on_submit`.

When used inside a modal or detail panel with a published subject (e.g. clicked `row`), binds to fields on that subject directly without a `data:` block.

## Example

```yaml
# Editing a description field in a detail modal
- component: textarea
  config:
    field: description
    label: Description
    rows: 5
    read_only: false
  on_blur:
    action: save_data_item
    collection: prospects
    key: "$: prospect_id"
    data_field: description

# Read-only display of a field (format applied if specified)
- component: textarea
  config:
    field: audit_notes
    label: Audit Trail
    rows: 4
    read_only: true
```

## Gotchas

- **Full snapshot on no `data_field`:** If the textarea sits in a Button/Modal footer with `on_click` and no `data_field` specified, it auto-attaches the full DataRef snapshot as the patch. Avoid surprises — use `data_field` to save only the textarea value, or wrap the textarea + button in a separate form context.
- **Rows is visual only:** The `rows` config sets the DOM `rows` attribute; tall content still scrolls. CSS `style: { maxHeight, overflowY }` can override if needed.
- **Preserve siblings:** The write merges shallowly — other fields on the same row are never wiped.

See `Composable UI Reference` > `Data binding`, `Actions`, `Which leaves can read a published subject` for context on DataRef sharing and write semantics.
