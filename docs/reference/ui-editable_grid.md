# ui-editable_grid

## Purpose
A spreadsheet-style matrix with configurable data-entry rules. Rows are one thing, columns are another thing, and each cell is the editable intersection of the two — jobs × days, people × skills, product × region, resource × week.

Three **independent** bindings make it generic rather than shaped for any one use case:

| Binding | Question it answers |
|---|---|
| `config.rows` | what are the rows? |
| `config.columns` | what are the columns? |
| `config.cells` | where does a cell's value physically live? |

Edits dispatch the canonical `save_data_item` action — no new built-ins, no bespoke fetches.

## When to use / when NOT
**Use:** entity-addressed matrices where every cell means *(this row-thing, this column-thing)* — timesheets, capacity planning, rate cards, allocation, availability, scoring rubrics, skills matrices.

**NOT:**
- A list of records with per-column formatting → `table`.
- A free-address spreadsheet (A1:Z99, cells with no entity meaning) → not this component; cells here are always addressed by two entities.
- A formula/dependency graph (`=B2*C4` recalc chains) → `derived` handles per-cell computation only; anything deeper belongs in an agent.
- Hierarchical or grouped rows with subtotals → axes are flat by design; use `tree_editor` for hierarchy.

## YAML shape
```yaml
component: editable_grid
data:
  collection: <cell collection>     # the CELL store binding
  state: draft
filter:                             # optional — narrow the cell rows first
  member: "$: subject.email"
config:
  rows:    { ... }                  # axis block (see below)
  columns: { ... }                  # axis block (see below)
  cells:   { ... }                  # storage topology (see below)
  cell_rules: { ... }               # data-entry rules
  rules_axis: column                # which axis cell_rules keys refer to
  totals: { rows: true, columns: true, grand: true }
  row_label: Project                # header text for the first column
  empty_text: Nothing logged yet.
```

## Axes — `config.rows` / `config.columns`
Both axes take the **same** block. Either can come from any of four sources.

| Key | Meaning |
|---|---|
| `source` | `static` \| `collection` \| `field` \| `distinct` (default `static`) |
| `items` | `source: static` — `[{id, label}]`, or bare strings |
| `collection` / `state` | `source: collection` — one axis entry per row |
| `field` | `source: field` — dot-path to an array on the active record. `source: distinct` — the cell-row field to collect distinct values of |
| `id_field` | identifier field (default `_key` for `collection`, `id` for `field`) |
| `label_field` | display field (default `title` / `label`; on `distinct` it names a *denormalised label carried on the cell rows* — no second fetch) |
| `sort` | `label` \| `id` \| `none` (default `none` — authored/observed order) |
| `add` | `{label, collection, state, id_field, label_field}` — picker that appends an axis entry at runtime |
| `remove` | `{confirm}` — × per entry that **clears that entry's cells** (`confirm` default `true` → inline two-step) |

`remove` never deletes the axis's own identity row: removing *Jane* from a people × week grid clears Jane's cells, it does not delete Jane. So the entry disappears only when the axis is derived from the cells it just cleared (`source: distinct`) or was added at runtime — with `source: collection` / `static` / `field` the entry stays, now empty. Which topologies support it:

| `cells.store` | `rows.remove` | `columns.remove` | How |
|---|---|---|---|
| `cell_object` | ✓ | ✓ | `delete_data_item` per matching cell row |
| `row_object` | ✓ | ✗ | deletes that grid row's tf row |
| `column_object` | ✗ | ✓ | deletes that grid column's tf row |
| `single_object` | ✓ | ✓ | one `save_data_item` patching the matrix |

An unsupported combination renders no control and logs one console warning — the other axis of `row_object` / `column_object` is a field name repeated on every row, so clearing it would be a bulk rewrite behind an × button.

`source: distinct` is the pivot case: the axis is whatever ids already appear in the cell store. Combined with `add:`, that gives an axis that **grows over time** — the user picks a new entry, fills a cell, and it persists from then on because a cell row now carries that id.

> Axis entries added via `add:` live in component state until a cell is written for them. Reloading before entering any value drops the empty row.

## Cell storage — `config.cells`
`store` picks one of four topologies. All four read and write through `save_data_item`.

| `store` | One tf row holds… | A cell edit writes | Per-cell patch? |
|---|---|---|---|
| `cell_object` *(default)* | one cell | `save_data_item` on that cell's row | ✓ sparse, cleanest |
| `row_object` | one grid row; column ids are its fields | `{ <columnId>: value }` merged onto the row | ✓ |
| `column_object` | one grid column; row ids are its fields | `{ <rowId>: value }` merged onto the row | ✓ |
| `single_object` | the whole matrix, nested under one field | the full patched matrix | ✗ whole-matrix save |

| Key | Meaning |
|---|---|
| `store` | topology, as above |
| `collection` / `state` | where cells live (defaults to `data.collection`) and the state written on save |
| `field` | single-field sugar — the one value a cell holds |
| `fields` | multi-field cells, e.g. `[hours, {field: notes}]`. First entry is primary (drives totals) |
| `key` | which tf row holds this cell — a literal or a `$:` expression with `row` and `column` in scope. Defaults: `<rowId>__<columnId>` (`cell_object`), row id (`row_object`), column id (`column_object`). **Required** for `single_object` |
| `row_field` / `column_field` | `cell_object` — the fields the row/column ids are stamped onto (default `row_id` / `column_id`). On `single_object`, `row_field` selects the **array matrix** (below) |
| `matrix_field` | `single_object` — the field holding the nested matrix (default `cells`) |

`cell_object` reads by matching the `(row_field, column_field)` pair rather than by re-deriving `key`, so changing the key expression never orphans existing rows on read.

### `single_object` — object matrix vs array matrix

The nested matrix comes in two shapes; `row_field` picks which.

**Object matrix** (no `row_field`) — keyed by row id, then column id:

```yaml
cells:
  store: single_object
  key: "$: week_key"
  matrix_field: cells        # cells: { acme: { mon: 4, tue: 0 }, ... }
  field: hours
```

**Array matrix** (`row_field` set) — a LIST of records, each naming its own grid row; column ids are plain fields on the record:

```yaml
cells:
  store: single_object
  key: "$: week_key"
  matrix_field: lines        # lines: [ { opportunity_slug: acme, mon: 4, tue: 0 }, ... ]
  row_field: opportunity_slug
  field: hours
```

Use the array form when a factory agent already owns that list — the grid reads and writes the agent's own shape, with no matrix transform on either side. Editing a cell for a row with no element yet APPENDS one (`{ <row_field>: <rowId>, <columnId>: value }`). A `rows: { source: distinct }` axis pivots over the array's elements, so `field: opportunity_slug` gives one grid row per line.

## Data-entry rules — `config.cell_rules`
A map keyed by axis id, with `*` as the fallback. `rules_axis` (default `column`) picks which axis the keys name. For multi-field cells the lookup order is `<axisId>.<field>` → `<axisId>` → `*.<field>` → `*`.

| Rule key | Effect |
|---|---|
| `type` | `number` \| `text` \| `select` \| `date` \| `checkbox` — picks the editor |
| `min` / `max` / `step` | numeric bounds and increment |
| `required` | blank is rejected |
| `pattern` | regex the value must match |
| `max_length` | string length cap |
| `options` | `select` choices (`[value]` or `[{value, label}]`) |
| `placeholder` | empty-cell hint |
| `editable: false` | hard-locks the cell |
| `editable_when` | `$:` predicate over `{row, column, value}` |
| `derived` | `$:` expression → read-only computed cell (excluded from totals) |
| `fill` | cell background — a literal CSS colour, or a `$:` expression over `{row, column, value}` |

`fill` paints the whole cell, so it is read from the **primary** field's rule; `value` is what the cell displays, which for a `derived` cell is its computed output. The component ships no palette: return your own hex or a theme custom property, and return nothing (`''`/`null`) for "no fill".

```yaml
cell_rules:
  "*":
    type: number
    min: 0
    max: 1.5
    step: 0.1
    # over-allocated red, idle amber, otherwise unpainted. Translucent so the
    # tint reads on either theme; `var(--status-error)` etc. are solid and too
    # strong behind cell text.
    fill: "$: value > 1 ? 'rgba(239,68,68,0.18)' : (value < 0.2 ? 'rgba(245,158,11,0.18)' : '')"
```

Rules are enforced **at the edge**: a failing edit is not committed, and the cell shows an error border. They shape input; they are not a security boundary. The agent consuming the collection stays the authority on what it accepts.

## Data & events
- Cell edit → `save_data_item` on blur or Enter (Escape reverts). The optimistic value shows immediately; totals update with it.
- `checkbox` / `select` commit on change.
- `remove` → `delete_data_item` per cell row (`cell_object`), one identity-row delete (`row_object` / `column_object`), or one `save_data_item` matrix patch (`single_object`). Runtime-added entries also vanish locally.
- Top-level `filter:` narrows the bound cell rows *before* the grid indexes them — this is how one person's slice of a shared collection is isolated. Two forms: a **map** of `field: value` pairs (equality, resolved once against the DataRef — shown above), or a single **`$:` predicate string** evaluated per row with that row's own fields merged over the DataRef. Reach for the predicate form when the test isn't equality on a stored field — e.g. a key-prefix test, `filter: '$: $substring(_key, 0, $length(subject.slug) + 1) = subject.slug & "#"'`, which is the robust way to scope a compound-keyed collection: a cell the grid has just written carries only the fields the cell store stamps, so a field filter would drop it on the next refetch and the number would appear to vanish, whereas the key always carries the prefix.
- No `on_<event>` handlers: downstream effects belong in `tf.on_state(collection, state)` agents.
- `totals` sums the **primary** field only, skipping `derived` cells.

## Example — timesheet (jobs as rows, days as columns, one row per cell)
```yaml
component: editable_grid
data:
  collection: time_entry
  state: draft
filter:
  member: "$: subject.member"
config:
  row_label: Project
  rows:
    source: distinct
    field: opportunity_slug
    label_field: opportunity_title
    sort: label
    add:
      label: Add project
      collection: opportunities
      label_field: title
  columns:
    source: static
    items:
      - { id: mon, label: Mon }
      - { id: tue, label: Tue }
      - { id: wed, label: Wed }
      - { id: thu, label: Thu }
      - { id: fri, label: Fri }
  cells:
    store: cell_object
    collection: time_entry
    state: draft
    fields:
      - hours
      - { field: notes }
    # `row` / `column` here are the GRID AXES, not a click subject — this is the
    # one place `row.` is permanent and correct. Do NOT rewrite it to `data.`
    # (see ui-common § `data`); `check_ui` deliberately does not flag it.
    key: "$: subject.member & '__' & subject.week_start & '__' & row.id & '__' & column.id"
    row_field: opportunity_slug
    column_field: day
  cell_rules:
    "*":       { type: number, min: 0, max: 24, step: 0.25, placeholder: "0" }
    "*.notes": { type: text, max_length: 120, placeholder: note }
  totals: { rows: true, columns: true, grand: true }
```

## Example — people × skills (one row per person)
Same leaf, nothing time-shaped: the row axis is a collection, each person's row *is* the record, and each skill is a field on it.

```yaml
component: editable_grid
data:
  collection: people
config:
  row_label: Person
  rows:    { source: collection, collection: people, label_field: name, sort: label }
  columns:
    source: static
    items:
      - { id: python,   label: Python }
      - { id: sql,      label: SQL }
      - { id: k8s,      label: Kubernetes }
  cells:
    store: row_object
    collection: people
    field: level
  cell_rules:
    "*": { type: select, options: [none, learning, working, expert] }
```

## Gotchas
- **`single_object` cannot patch one cell** — every edit rewrites the whole matrix (or the whole array), so concurrent editors clobber each other. Use it only for a single-writer document; prefer `cell_object` for live entry.
- **`save_data_item` runs no agent code**, so nothing a `single_object` row derives from its matrix (a per-line subtotal, a grand total field) is recomputed by the write. Recompute it in the agent that reads the row, not in the grid.
- `row_object` / `column_object` store one cell per **field name**, so column ids (or row ids) must be safe field names — no dots.
- A blank cell in `cell_object` still writes a row with a null value; it does not delete the row.
- Axis entries added at runtime are local until a cell is written for them.
- `remove` clears cells, it does **not** delete the axis's identity row — on a `source: collection` axis the entry stays visible and empty. Want it gone? Pivot the axis off the cells (`source: distinct`).
- `remove` on `cell_object` deletes by each cell row's own key, so a changed `cells.key` expression never leaves rows it can no longer name.
- `derived` and `editable_when` use `$:` JSONata (see **ui-common**) evaluated against `{row, column, value}` — no cross-cell references beyond those three.
- Wide grids scroll horizontally inside the component; the row-label column is sticky.
