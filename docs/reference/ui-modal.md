# ui-modal

## Purpose

A surface that opens and closes over the main page, commonly for editing detail records, confirming actions, or presenting forms. Modals wrap content in `TransientHostContext` (framework primitive), which tells nested stateful components (Tabs, filters, search) to store UI state **locally and transiently** — resetting on each open — rather than persisting to the URL.

## When to use / when NOT

**Use when:** focused detail/edit workflows (single entity), short-lived interactions, keyboard shortcuts needed (Escape, ⌘/Ctrl+S).

**Avoid when:** complex enough for a dedicated route, or you need stateful UI (Tabs, sort, filter) to persist across opens.

## Placement — declare modals in `default_ui.modals`

A modal is **React-portaled and opened by `id:`** — it resolves against the page-level id registry, not literal DOM siblings, so its position in the layout is irrelevant as long as it's **mounted** when its trigger fires. It renders **out of flow**, so it is NOT an in-flow layout child.

**Declare modals in the top-level `default_ui.modals` array** — a sibling of `default_ui.layout`, keyed by `id:`. They mount page-level (inside the same data context as the layout), so ANY trigger — a button, a table `on_row_click`, or the chat `open_ui_modal` tool — opens one by id, and ONE modal def is reusable from many trigger sites.

```yaml
default_ui:
  layout:
    component: card
    title: Clients
    children:
      - component: table
        data: { collection: client, state: active }
        on_row_click: { open: client_modal }     # opens the modal below by id
  modals:
    - component: modal
      id: client_modal
      title: '$: row.name'
      body: [ ... ]
    - component: modal
      id: new_client_modal
      title: New client
      body: [ ... ]
```

- **DEPRECATED — nesting a modal in the layout tree.** A `modal` placed inside a `card` / `layout_column` / tab panel still works but is deprecated (`edit_ui` emits a warning) and will be removed in a future version. Move it to `default_ui.modals`.
- **NEVER wrap the layout in a `layout_column` just to sit a modal beside a table/card** — modals aren't in-flow, so that wrapper wraps a single child: a `redundant-singleton` (see `ui-common` § `redundant-singleton`) that `edit_ui` REFUSES. `default_ui.modals` removes any need for such a wrapper, and keeps a root `tabs` as the single root node.
- A modal opened from several places (several tabs, a row + a button) is declared **once** in `default_ui.modals` and opened by id from each.

## YAML shape

Two forms supported. **Named-key form (current default):**

```yaml
component: modal
title: Detail Title
config:
  width: 720px
  max_height: 80vh
  show_close_button: true
  close_on_escape: true
body:
  - component: tabs
    children: [...]
footer:
  - component: button_group
    children: [...]
```

**Legacy slot form (back-compat):**

```yaml
component: modal
title: Detail Title
config:
  width: 720px
  max_height: 80vh
  show_close_button: true
  close_on_escape: true
children:
  - slot: body
    component: tabs
    children: [...]
  - slot: footer
    component: button_group
    children: [...]
```

Slot-aware: children may carry `slot: header|body|footer` (default `body`; legacy form only). Named-key form (`body:`, `footer:`) is preferred.

## Config keys

| Key | Type | Default | Notes |
|---|---|---|---|
| `width` | CSS length | — | e.g. `720px`, `100%`. |
| `max_height` | CSS length | — | e.g. `80vh`. |
| `min_height` | CSS length | — | e.g. `400px`. |
| `height` | CSS length | `auto` | e.g. `auto`, `80vh`. |
| `show_close_button` | bool | `true` | X button in header. |
| `close_on_escape` | bool | `true` | Press Escape to close. |
| `close_on_backdrop` | bool | `true` | Click outside modal to close. |

## Data & events

**Built-in actions:**
- `{ action: close }` — closes the modal.
- **Persist a record — `save_data_item` / `delete_data_item`** (with `then_close: true`) — a footer that writes a factory_data row (edit an existing record, `key: '$: row._key'`; or create a new one, `key: $uuid`). This is the canonical CRUD footer for a data-backed modal. A Button/Modal-footer with `save_data_item` and no explicit `data` auto-attaches the DataRef snapshot, so the body's form fields are written back. See the full **table → tabbed detail/edit modal → footer CRUD → add-item** pattern in `read_docs{ doc: "ui-table" }` (§ CRUD).
- `{ action: custom:save, then_close: true }` — bubbles to the HOST PAGE's `onAction` (a bespoke-page hook). Factory `default_ui` has no such host handler, so `custom:*` is a NO-OP there — use `save_data_item` to persist. (See `ui-common` for open/close action detail.)

**Keyboard shortcuts (zero config):**
- **Escape** — closes the modal (unless `close_on_escape: false`).
- **⌘+S** (mac) / **Ctrl+S** (win/linux) — fires the primary footer button. Auto-detected: framework finds the first footer button with `config.variant: primary` (or `config: { submit: true }` to override). Same handler path as a real click; `then_close` still applies. If no primary button exists, browser saves normally.

## Example

```yaml
component: modal
title: Edit Record
config:
  width: 720px
  max_height: 80vh
  show_close_button: true
body:
  - component: tabs
    children: [...]
footer:
  - component: button_group
    children:
      - component: button
        config: { label: Cancel, variant: secondary }
        on_click: { action: close }
      - component: button
        config: { label: Save, variant: primary }
        on_click: { action: save_data_item, collection: client, key: '$: row._key', then_close: true }
```

For a tabbed record modal opened from a table row (overview / edit / related records), and the add-new-item form-modal pattern, see `read_docs{ doc: "ui-table" }` § CRUD — it shows the composite end to end.

## Gotchas

- **No primary button:** If a modal has no primary footer button (and no `submit: true` button), ⌘/Ctrl+S is a no-op.
- **Transient state:** Nested components (e.g. Tabs, search fields) reset state on each open. If this is confusing (e.g. sub-tabs from a different row's previous open), ensure the component reads `TransientHostContext` to opt into transient storage.
- **Detail modal:** Tables also provide a detail modal; both use the same transient-host contract.
