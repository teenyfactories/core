# ui-table

## Purpose

Flagship data-display component: sortable, paginated table over a factory_data collection, with per-column rendering, row actions, and a row-click detail view (sibling `modal` id-ref, or legacy inline `detail_modal`). Row click is the universal `on_item_click:` handler.

## When to use / when NOT

**Use:** multi-row collection views with column headers; server-correct sort/pagination over a whole collection; row-level actions or click-through to a full record.

**NOT:** a single record's fields → `ui-detail_list`. Free-form editing outside a table → standalone form leaves. See `ui-common` for the shared action enum and `data:` binding modes.

## YAML shape

```yaml
component: table
data: { collection: invoices, state: pending }
config:
  key_field: invoice_id
  page_size: 50
  sort_field: fetched_at     # data key OR meta field — see Data & events
  sort_dir: desc
  max_rows: 100
  columns:
    - { field: vendor.name, label: Vendor, truncate: 30, sortable: false }
    - { field: title, label: Title, link_field: url }
    - field: status
      label: Status
      type: tags
      tagColor: primary
      value: "$: $uppercase(row.status)"
    - { field: total_amount, label: Amount, format: number }
  row_actions:
    - { icon: check, action: save_data_item, collection: review_queue, key: "$: invoice_id", state: approved }
    - { icon: eye, action: open, id: invoice_detail_modal }
on_item_click:           # TOP-LEVEL on the component — a SIBLING of `config:`, NOT inside it
  open: invoice_detail_modal
```

> **Naming:** `on_item_click` is the universal handler for "the user clicked one of the things
> this component renders" — a row here, a point on a `scatter`. One spelling across leaves, so a
> handler reads the same wherever it's copied. The table-only `on_row_click:` still fires but is
> **deprecated** (`check_ui` warns).
>
> **Placement:** `on_item_click` is read at the component top level (`component.on_item_click`), a
> sibling of `data:` / `config:` — **not** a key inside `config:`. Nesting it under `config:`
> disables row-click at runtime (rows aren't clickable). `check_ui` rejects this
> (`misplaced-under-config` error — see `ui-common`), so `edit_ui` refuses the save. `row_actions`
> DOES live inside `config:`; `on_item_click` does not — don't mirror its placement.
>
> `row_actions` is a config list, not an `on_<event>` handler, which is why the two differ.
> A **top-level** `row_actions:` still renders but is **deprecated** — `check_ui` emits a
> warning and it will be removed in a future version. Author it inside `config:`, beside
> `columns:`, as shown above.

## Config keys

| Key | Default | Effect |
|---|---|---|
| `key_field` | `id` | Row dedup key. |
| `page_size` | `50` | Rows/page. |
| `max_rows` | `100` | Cap on total rows paginated. |
| `sort_field` | `null` | Default server sort field. |
| `sort_dir` | `asc` | Sort direction (`asc` or `desc`). |
| `empty_text` | — | Zero-rows message (overrides the default empty-state copy). |
| `columns[].field` / `.label` | Dot-path into row data (nested ok, also sort key) / header text. |
| `columns[].truncate` | Max chars, then ellipsis. |
| `columns[].sortable: false` | Excludes column from header-click sort — for columns lacking a backing field. |
| `columns[].link_field` | Renders cell as link, field's value as `href`. |
| `columns[].format` | `number` \| `percentage` \| `relative_time` \| `currency`. |
| `columns[].value: "$: <expr>"` | JSONata over `{ row }`, replaces `field:` for display; `field:` still needed for sort/name. Builtins incl. `$uppercase`, `$string`, `&`, ternary. This `row` is the **cell-render** context and is NOT the deprecated click-subject prefix — keep it. |
| `columns[].type: tags` | Cell → pill chip(s) — array → chip/item, scalar → single chip. |
| `columns[].tagColor: primary` | Only with `type: tags`; solid fill, white text. Omit for neutral; reserve for status pills. |
| `sentiment_field` | String, default `'sentiment'`. Data key for tracking active row-action index (internal). |
| `row_actions[]` | Flat sibling params (`action:`, `collection:`, `key:`, `state:`/`id:`, `icon:`, `label:`), canonical action enum — no `args:`/`actions:` wrapper. |
| `on_item_click.open` | Id-ref (string/`$:` JSONata → string) to sibling `modal` — recommended shape. |
| `on_item_click.action` | Dispatch a canonical action on row click (e.g. `save_data_item`, `delete_data_item`). |
| `on_item_click.detail_modal` | Legacy inline shape (below), still supported. |
| `on_row_click` | **Deprecated** alias of `on_item_click` — still fires, `check_ui` warns. |

## Data & events

- **Sort — server-side only** (Table paginates; client-side sort would only reorder the loaded page). `sort_field`/`sort_dir` set default order, forwarded as query params (`ORDER BY` over all rows). Headers are clickable — click sorts by column, click again toggles `asc`⇄`desc`; active column shows ▲/▼. Meta-field map: `_updated_at`→`updated_at`, `_created_at`→`created_at`, `_state`→`state`, `_key`→`key`; else → `value->>'<field>'`. Omit → default `updated_at DESC`.
- **Row click** publishes the clicked row onto DataRef so descendants resolve `$: data.field` — the same prefix a card, scatter or force-graph click publishes under, so a modal body doesn't care which leaf opened it. The legacy table-only spelling `$: row.field` still works and is deprecated (`check_ui` warns). Inside `on_item_click`'s own params, `$: data.field` and bare `$: field` both resolve. `on_item_click.open` / `row_actions[*].action: open` take a string id only (id-ref pattern; `ui-common`).
- **`detail_modal` (legacy).** Portal-mounted, scoped to the clicked row; body + footer share one hoisted DataRefProvider — footer Save auto-sees body edits, no `data:`/`data_field:` needed (`key: '$: _key'`; `_key`/`_state`/`_updated_at` are Table-injected). Sibling id-ref is newer/recommended; `detail_modal:` predates it, still supported. **Known debt:** footer Save's snapshot leaks those underscore keys into saved `data` JSONB (display unaffected, re-flattened next read) — tracked in the composable-ui debt register (`memory/composable-ui/`).
- **`sections:`** >1 entry → tab strip (`title` = label, footer visible across tabs); 1 entry → inline. Sizing `config: { width, max_width, max_height, min_height }` (defaults `800px`/`90vw`/`85vh`/unset) — set `min_height` if tab heights vary, else the modal collapses/jumps.
- **Field asymmetry**: `markdown`/`table` read `field` at section top level; everything else reads it from `config:`. `table.field` needs array of **objects**; array of **strings** needs `tag_list`. Detail modal table sections also accept `columns: [{field, label}]` to control column headers.
- `delete_data_item` + `then_close: true` = canonical row-remove; no built-in confirm step for `detail_modal` footer actions (`confirm_destructive_modal` not wireable there).

## Examples

Tabbed `detail_modal`, destructive footer:

```yaml
on_item_click:
  detail_modal:
    title: '$: filename'
    config: { width: 760px, max_height: 80vh, min_height: 480px }
    sections:
      - { title: Summary, component: markdown, field: summary }
      - { title: Covered events, component: tag_list, config: { field: covered_events, variant: filled } }
      - { title: Policy, component: code_editor, config: { field: full_text, language: text, read_only: true } }
    footer:
      - component: button
        config: { label: Delete & re-index, variant: danger, icon: trash }
        on_click: { action: delete_data_item, collection: product, key: '$: filename', then_close: true }
```

## CRUD completeness — make interactive data actually interactive

The goal is **CRUD-completeness for collections users should act on**: if a collection is meant to be created / edited / actioned, a bare display-only table isn't enough — give it those affordances. A row-click detail/edit modal is a **good, common mechanism** for this, but it is NOT mandatory — `row_actions`, inline forms, a kanban board, or an add-item button on their own can also carry the create/edit/act path. Genuinely read-only data (reports, metrics, logs) is fine as a plain table. The shapes below are the recommended mechanism for the common "browse + open + edit + add" case.

### Table → sibling TABBED detail modal (a good CRUD mechanism; recommended over the legacy `detail_modal:` above)

Declare the record view as a SIBLING `modal` with an `id:`; the table's `on_item_click: { open: <id> }` publishes the clicked row onto the DataRef, so the modal's descendants resolve `$: data.<field>`. (The table-only spelling `$: row.<field>` still resolves and is **deprecated** — `check_ui` warns. See `ui-common` § `data`.) A record modal is a `tabs` block (tab/panel pairs), not a flat `detail_list`, and its `footer` carries the CRUD actions (edit-save, delete):

```yaml
# The record modal below is React-portaled (opened by id) — it is NOT a flex
# sibling of the table and needs NO layout wrapper; it only has to be MOUNTED in
# the same view. PREFER declaring it in the top-level `default_ui.modals` array
# (page-level, keyed, reusable from many triggers — see ui-modal); nesting it in
# the layout (as shown below) still works but is DEPRECATED (edit_ui warns).
# NEVER add a `layout_column`/`layout_row` just to sit a modal beside a table —
# that's a redundant singleton (see ui-common § redundant-singleton) that edit_ui
# refuses. Keep a root `tabs` as the SINGLE root node.
- component: card
  title: Clients
  children:
    - component: table
      data: { collection: client, state: active }
      config:
        key_field: _key
        columns:
          - { field: name, label: Client }
          - { field: industry, label: Industry }
          - { field: status, label: Status, type: tags }
      on_item_click: { open: client_modal }         # opens the sibling modal below

    - component: modal
      id: client_modal
      title: '$: data.name'                       # `data.` = whatever was clicked
      config: { width: 820px, max_height: 85vh }
      body:
        - component: tabs
          children:
            - { component: tab, slot: tab, title: Overview, config: { icon: id-card } }
            - component: detail_list
              slot: panel
              config:
                fields:
                  - { field: data.name, label: Name }
                  - { field: data.industry, label: Industry }
                  - { field: data.owner, label: Account owner }
            - { component: tab, slot: tab, title: Edit, config: { icon: pen } }
            - component: layout_column
              slot: panel
              children:
                - component: text_input
                  config: { field: name, label: Name }
                - component: select
                  config:
                    field: status
                    label: Status
                    options: [ { value: active, label: Active }, { value: churned, label: Churned } ]
            - { component: tab, slot: tab, title: Opportunities, config: { icon: briefcase } }
            - component: table
              slot: panel
              data: { collection: opportunity, state: open }
              config:
                columns: [ { field: title, label: Opportunity }, { field: value, label: Value, format: currency } ]
      footer:
        - component: button
          config: { label: Delete, variant: danger, icon: trash }
          on_click: { action: delete_data_item, collection: client, key: '$: data._key', then_close: true }
        - component: button
          config: { label: Save, variant: primary, icon: check }
          on_click: { action: save_data_item, collection: client, key: '$: data._key', then_close: true }
```

- **Tabs shape:** `tabs.children` is an alternating sequence of `{ component: tab, slot: tab, title: … }` markers and their panel node (`slot: panel`). One record modal, several perspectives (overview / edit form / related records) — this is the "rich tabbed modal", not multiple flat modals.
- **Edit:** the Edit tab's inputs bind form fields with `field:`; the footer **Save** (a Button/Modal-footer with `save_data_item` and no explicit `data`) auto-attaches the DataRef snapshot, writing the edited fields back to `key: '$: data._key'` (the clicked row).
- **Delete:** `delete_data_item` on `key: '$: data._key'` + `then_close: true`.

### Add NEW item — every creatable collection needs a create path

A read-only table with no way to add a row is incomplete. Give the collection a `plus` button that opens a blank FORM modal whose Submit writes a FRESH row (`key: $uuid`) at the collection's entry state:

```yaml
- component: button
  config: { label: New client, variant: primary, icon: plus }
  on_click: { action: open, id: new_client_modal }

- component: modal
  id: new_client_modal
  title: New client
  config: { width: 640px }
  body:
    - component: layout_column
      children:
        - component: text_input
          config: { field: name, label: Name, required: true }
        - component: select
          config:
            field: industry
            label: Industry
            options: [ { value: fintech, label: Fintech }, { value: health, label: Health } ]
  footer:
    - component: button
      config: { label: Cancel, variant: secondary }
      on_click: { action: close }
    - component: button
      config: { label: Create, variant: primary, icon: plus }
      on_click: { action: save_data_item, collection: client, key: $uuid, state: active, then_close: true }
```

- `key: $uuid` mints a fresh row per click — NEVER a fixed key (collides), NEVER hard-coded placeholder data in place of a form the user fills.
- For a create with no fields to collect, skip the modal — the `plus` button dispatches `save_data_item` with `key: $uuid` directly.
- The same `key: $uuid` + entry-state pattern is how a UI **triggers an agent** (write a fresh row at the state the agent's input link watches).

## Gotchas

- Client-side sort is wrong here — always `sort_field`/`sort_dir`; `sortable: false` only for columns lacking a backing field.
- A collection users are meant to create/edit/action, shipped as a display-only table with no create/edit path, is the gap — add one of the CRUD mechanisms above. (Genuinely read-only data as a plain table is fine and not a defect.)
- `tagColor: primary` = status/disposition pills only, never free-form tag-lists.
- `table` sections need array of objects, not strings — use `tag_list` for strings.
- `code_editor` (`language: text`) beats `markdown` for raw blobs — markdown corrupts stray `#`/`-`/`|`/numbered lines.
- No confirm step on `detail_modal` footer destructive actions; `_key`/`_state`/`_updated_at` leak (known debts, don't patch inline).
