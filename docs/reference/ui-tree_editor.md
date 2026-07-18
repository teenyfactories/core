# ui-tree_editor

## Purpose
Hierarchical tree editor for flat collections. Displays parent-child relationships via `parent_id` pointers; supports add, rename, and delete operations with client-side tree construction.

## When to use / when NOT
**Use:** Folder structures, org charts, hierarchies, nested workflows.
**NOT:** Deep nesting (→ `max_depth` cap), pre-built nested JSON (use tree_view for read-only), frequent bulk tree mutations.

## YAML shape
```yaml
component: tree_editor
data:
  collection: <collection_name>   # flat rows; one per node
  state: active                   # binds the live set
config:
  id_field: id                    # row key (default 'id')
  name_field: name                # display label (default 'name')
  parent_field: parent_id         # parent pointer (default 'parent_id'; null = root)
  order_field: display_order      # optional sibling sort
  add_state: active               # state for new nodes
  max_depth: 5                    # nesting limit
  labels: { add: Add, edit: Rename, delete: Remove }
```

## Config keys
- `id_field` — Row identifier (unique).
- `name_field` — Display text column.
- `parent_field` — Points to parent's `id` value; `null` / absent = root node.
- `order_field` — Sorts siblings (optional).
- `add_state` — Initial state for new nodes (fallback: bound `state` or `'active'`).
- `max_depth` — Max nesting levels; UI enforces this limit.
- `labels` — Button text; defaults: `{ add: Add, edit: Rename, delete: Remove }`.

## Data & events
- **Add** → fires `save_data_item` (new row at `state`).
- **Rename** → fires `save_data_item` (existing row updated).
- **Delete** → fires `delete_data_item`; client cascades (walks descendants, N deletes).
- **No event handlers** — side effects live in agents listening to `tf.on_state(collection, state)`.

## Example
```yaml
component: tree_editor
data:
  collection: org_structure
  state: active
config:
  id_field: emp_id
  name_field: full_name
  parent_field: manager_id
  order_field: sort_key
  max_depth: 8
  labels: { add: Add Role, edit: Rename, delete: Remove }
```

## Gotchas
- Data is **flat**; tree built client-side from `parent_id` pointers.
- Delete cascades hard—no confirmation. Client walks descendants and fires deletes for all.
- `max_depth` is UI-only; enforce in agents if persistence matters.
- No UI event handlers; put business logic in state listeners (`tf.on_state`), not handlers.
