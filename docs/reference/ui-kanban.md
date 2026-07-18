# ui-kanban

**Purpose**  
Renders a collection as state-grouped kanban columns: each column represents one state, each card is a factory_data row. Dragging a card between columns writes the row's new state.

**When to use / when NOT**  
Use when you need to visualize and manually manage rows by state (workflows, task boards, pipelines). NOT for read-only state visualization; NOT when state changes must be guarded or restricted.

**YAML shape**
```yaml
component: kanban
data:
  collection: <name>        # ALL rows; NO state filter
config:
  columns:                  # Ordered list; order = display order
    - { state: <name>, label: <text>, color: <hex|token>?, empty_text: <text>? }
children:
  - component: card         # exactly one, per-row template
```

**Config keys**
- `columns` (required): List of column definitions, each mapping to one state; display order matches list order.
  - `state` (required): State value for this column.
  - `label` (required): Column header text.
  - `color` (optional): Hex color or token for column header top border.
  - `empty_text` (optional): Message shown when column has no cards.
- `children` (required): Exactly one child, `component: card`. Card leaves read fields flat (`field: title`, not `field: row.title`). Row metadata exposed as `_key`, `_state`, `_updated_at`.

**Data & events**
- Fetches ALL rows in collection, groups by state; no state filter in `data:`.
- Cards within a column ordered by `updated_at`, most recent first.
- Rows with unmatched states are hidden.
- Drop dispatches `save_data_item` with target state, no data payload — state-only PUT fires `{factory}.{collection}.{state}` NOTIFY.
- No transition guards; any card can move to any column.
- Optimistic UI: card appears in target column immediately; server mismatch rolls back after timeout; concurrent server-side state wins.
- Drags on interactive leaves (`button`, `a`, `input`, `textarea`, `select`) suppressed — clicks not swallowed by drag.

**Example**
```yaml
component: kanban
data:
  collection: tasks
config:
  columns:
    - { state: todo, label: To do }
    - { state: in_progress, label: In progress, color: "#f59e0b" }
    - { state: done, label: Done, empty_text: Nothing done yet }
children:
  - component: card
    children:
      - component: markdown
        config: { field: title }
```

**Gotchas**
- Must provide at least one column.
- `children` must be exactly one `card` component.
- No state filter in `data:` — kanban fetches and groups all rows.
- Rows whose state doesn't match any declared column are silently hidden.
- No state transition hooks or guards; moves are optimistic and fire-and-forget.
