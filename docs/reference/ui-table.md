# ui-table

## Purpose

Flagship data-display component: sortable, paginated table over a factory_data collection, with per-column rendering, row actions, and a row-click detail view (sibling `modal` id-ref, or legacy inline `detail_modal`).

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
  on_row_click:
    open: invoice_detail_modal
```

## Config keys

| Key | Effect |
|---|---|
| `key_field` | Row dedup key. |
| `page_size` | Rows/page. |
| `max_rows` | Cap on total rows paginated. |
| `sort_field` / `sort_dir` | Default server sort. |
| `columns[].field` / `.label` | Dot-path into row data (nested ok, also sort key) / header text. |
| `columns[].truncate` | Max chars, then ellipsis. |
| `columns[].sortable: false` | Excludes column from header-click sort — for columns lacking a backing field. |
| `columns[].link_field` | Renders cell as link, field's value as `href`. |
| `columns[].format` | `number` \| `percentage` \| `relative_time` \| `currency`. |
| `columns[].value: "$: <expr>"` | JSONata over `{ row }`, replaces `field:` for display; `field:` still needed for sort/name. Builtins incl. `$uppercase`, `$string`, `&`, ternary. |
| `columns[].type: tags` | Cell → pill chip(s) — array → chip/item, scalar → single chip. |
| `columns[].tagColor: primary` | Only with `type: tags`; solid fill, white text. Omit for neutral; reserve for status pills. |
| `sentiment_field` | String, default `'sentiment'`. Data key for tracking active row-action index (internal). |
| `row_actions[]` | Flat sibling params (`action:`, `collection:`, `key:`, `state:`/`id:`, `icon:`, `label:`), canonical action enum — no `args:`/`actions:` wrapper. |
| `on_row_click.open` | Id-ref (string/`$:` JSONata → string) to sibling `modal` — recommended shape. |
| `on_row_click.action` | Dispatch a canonical action on row click (e.g. `save_data_item`, `delete_data_item`). |
| `on_row_click.detail_modal` | Legacy inline shape (below), still supported. |

## Data & events

- **Sort — server-side only** (Table paginates; client-side sort would just reorder the loaded page). `sort_field`/`sort_dir` set default order, forwarded as query params (`ORDER BY` over all rows). Headers are clickable — click sorts by column, click again toggles `asc`⇄`desc`; active column shows ▲/▼. Meta-field map: `_updated_at`→`updated_at`, `_created_at`→`created_at`, `_state`→`state`, `_key`→`key`; else → `value->>'<field>'`. Omit → default `updated_at DESC`.
- **Row click** publishes `{ row: <clonedRow> }` onto DataRef so descendants resolve `$:row.field`. `on_row_click.open` / `row_actions[*].action: open` take a string id only (id-ref pattern; `ui-common`).
- **`detail_modal` (legacy).** Portal-mounted, scoped to the clicked row; body + footer share one hoisted DataRefProvider — footer Save auto-sees body edits, no `data:`/`data_field:` needed (`key: '$: _key'`; `_key`/`_state`/`_updated_at` are Table-injected). Sibling id-ref is newer/recommended; `detail_modal:` predates it, still supported. **Known debt:** footer Save's snapshot leaks those underscore keys into saved `data` JSONB (display unaffected, re-flattened next read) — filed against composable-ui-architect debt register.
- **`sections:`** >1 entry → tab strip (`title` = label, footer visible across tabs); 1 entry → inline. Sizing `config: { width, max_width, max_height, min_height }` (defaults `800px`/`90vw`/`85vh`/unset) — set `min_height` if tab heights vary, else the modal collapses/jumps.
- **Field asymmetry**: `markdown`/`table` read `field` at section top level; everything else reads it from `config:`. `table.field` needs array of **objects**; array of **strings** needs `tag_list`. Detail modal table sections also accept `columns: [{field, label}]` to control column headers.
- `delete_data_item` + `then_close: true` = canonical row-remove; no built-in confirm step for `detail_modal` footer actions (`confirm_destructive_modal` not wireable there).

## Examples

Tabbed `detail_modal`, destructive footer:

```yaml
on_row_click:
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

## Gotchas

- Client-side sort is wrong here — always `sort_field`/`sort_dir`; `sortable: false` only for columns lacking a backing field.
- `tagColor: primary` = status/disposition pills only, never free-form tag-lists.
- `table` sections need array of objects, not strings — use `tag_list` for strings.
- `code_editor` (`language: text`) beats `markdown` for raw blobs — markdown corrupts stray `#`/`-`/`|`/numbered lines.
- No confirm step on `detail_modal` footer destructive actions; `_key`/`_state`/`_updated_at` leak (known debts, don't patch inline).
