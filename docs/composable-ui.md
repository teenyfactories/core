# Composable UI

Every factory ships a dashboard. You don't write React for it — you describe it in
YAML, under a `default_ui:` block in your `factory.yml`, and the orchestrator
renders it from a library of **composable components**: tables, charts, forms,
metrics, modals, a chat panel, and the layout primitives that arrange them.

This page is the authoring contract: how to write a `default_ui`, how components
bind to your factory's data, and how to wire buttons to actions. The headline
principle to keep in mind throughout:

!!! tip "The building blocks just work"
    Author your **desktop intent** with the layout primitives and let the engine
    infer the axis, size the children, own the padding/scroll/spacing, and go
    mobile on its own. You can build a full dashboard — including modals and
    detail panels — with **zero `style:` blocks**. `style:` is a last-resort
    raw-CSS escape hatch, not the default tool.

## What `default_ui` is

`default_ui` is a tree of components. The root is a single `layout:` node;
everything else nests under it via `children:`. A minimal dashboard:

```yaml
# factory.yml
default_ui:
  layout:
    component: card
    title: Hello
    children:
      - component: markdown
        config:
          field: body
        data:
          collection: docs
          state: published
          latest: true
```

Three ideas do all the work:

- **`component:`** names the leaf type (`card`, `table`, `line_chart`, …). It is
  the only required key on every block.
- **`data:`** binds a component to your factory's `factory_data` store — a
  collection, optionally filtered to a state. The component fetches, subscribes
  to live updates, and re-renders on change. You never write fetch code.
- **`config:`** holds the per-component options (which columns a table shows,
  which field a metric reads, and so on).

Everything beyond that — layout, actions, modals — is composition.

### Universal keys

Every component block draws from the same small set of top-level keys. The
renderer reads only these; anything component-specific lives under `config:`.

```yaml
component: table            # REQUIRED — the leaf type (snake_case)
id: orders_table           # Optional — a stable id, for cross-component reference
title: "Orders"            # Optional — header text on card / modal / tabs / metrics
data: { collection: orders }   # Optional — data binding (see Data binding)
filter: "$: total > 0"     # Optional — row-level winnowing
config: { ... }            # Per-component options
children: [ ... ]          # Optional — nested children (layout components)
on_click: { action: ... }  # Optional — event handlers (see Actions)
style: { ... }             # Optional — raw CSS escape hatch (last resort)
show_when: "$: is_ready"   # Optional — render only when the expression is truthy
```

!!! warning "`style:` and event handlers are top-level — never nested under `config:`"
    `style:`, `on_click:`, `data:`, and `filter:` are siblings of `component:`.
    Nesting them under `config:` is silently ignored. Likewise, action parameters
    are flat siblings of `action:` — there is no `args:` wrapper (see
    [Actions](#actions)).

## The layout system

Two primitives arrange everything. Learn these and the rest is composition.

| Component | Identity | Use it to… |
|---|---|---|
| `layout_row` | A horizontal flex row. Its axis is **fixed** (row). | Place children **side by side**. |
| `layout_column` | A vertical flex column. Its axis is **fixed** (column). | **Stack** children top to bottom. |

Both accept optional `config: { gap, align, justify }` (CSS values). You never
hand-write `flex`, `display`, or `flex-direction`.

!!! note "`grid` is legacy"
    Older factories use a `grid` component with `direction: horizontal|vertical`.
    It still renders, but **don't author new `grid` blocks** — use `layout_row` /
    `layout_column`. Migration is mechanical: `direction: horizontal` → `layout_row`,
    `direction: vertical` → `layout_column`, per-child `style: { flex: N }` →
    `config: { flex: N }`, and the hand-wired padding/overflow goes away (the
    region owns it).

### Axis inference for regions

A **region** is a host that lays out a block of children: a **modal body**, a
**tab panel**, or a **card body**. A region has no fixed axis — it *infers* the
axis from the **type of its children**:

| Region's children | Inferred axis | Result |
|---|---|---|
| contain `layout_column` siblings | **horizontal** | columns sit side by side |
| contain `layout_row` siblings | **vertical** | rows stack |
| only leaves (no layout primitives) | **vertical** | leaves stack |
| exactly **one** child | **vertical** | the single child **fills** the region |

The one rule to internalise: a block *containing* `layout_column`s goes
horizontal, because each column is vertical internally, so the columns belong
*next to* each other. You pick the child primitive based on how each child
should lay out internally, and the parent axis follows.

`layout_row` / `layout_column` are explicit and never inferred — only regions
infer.

!!! warning "no-mixed-axis"
    A region must **not** contain both a `layout_row` and a `layout_column`
    sibling — there is no single correct axis, so the engine refuses to guess.
    At render time it shows a visible "Layout error" banner; at factory-load time
    the validator rejects it. **Fix:** nest one inside the other.

    ```yaml
    # WRONG — a region with both a row and a column sibling.
    - component: card
      children:
        - component: layout_row
          children: [ ... ]
        - component: layout_column      # ✗ mixed with the layout_row sibling
          children: [ ... ]

    # RIGHT — wrap them so the region sees one axis.
    - component: card
      children:
        - component: layout_column      # a column of rows ⇒ vertical
          children:
            - component: layout_row
              children: [ ... ]
            - component: layout_row
              children: [ ... ]
    ```

    (Tabs are exempt — each `slot: panel` is its own separate region, so a row
    panel next to a column panel is fine.)

### Sizing children with `config.flex`

How a child of a `layout_row` / `layout_column` is sized depends on `config.flex`
**and the parent's axis**:

| `config.flex` | Behaviour |
|---|---|
| *(omitted)* in a **`layout_row`** | **Grow / split the width** equally with its siblings. This is why a row of columns splits the width with no per-child config. |
| *(omitted)* in a **`layout_column`** | **Content height — stack.** Children sit at their natural height, so a column of form fields isn't squished. |
| `flex: N` (N > 0) | Take **N shares of the leftover space** along the parent's axis **and fill the cross axis** — so a card/chart/table given `flex: 1` fills its whole box (both dimensions). Larger N ⇒ larger share. |
| `flex: 0` | **Size to content.** Use for a header strip, a `metrics` row, a `button_group`. |

The canonical dashboard column — a summary strip at its natural height, above a
chart that fills the rest:

```yaml
- component: layout_column
  children:
    - component: metrics
      config: { flex: 0 }          # content height — the summary strip
      data: { collection: kpis, latest: true }
    - component: card
      config: { flex: 1 }          # fills the remaining height AND width
      children:
        - component: line_chart
          data: { collection: revenue }
```

!!! note "Inside a `layout_column`, give a chart/table/card `flex: 1` to make it fill"
    A child with no `flex` in a column sits at *content height*. A `metrics` or
    `button_group` strip needs no config — but a chart/table you want to **fill**
    the column must be given `config: { flex: 1 }` explicitly.

A full two-column dashboard, no `style:` anywhere:

```yaml
- component: layout_row
  children:
    - component: layout_column
      config: { flex: 2 }          # left column: 2 shares of the width
      children:
        - component: card
          config: { flex: 1 }
          children: [ { component: line_chart, data: { collection: revenue } } ]
        - component: card
          config: { flex: 1 }
          children: [ { component: table, data: { collection: orders } } ]
    - component: layout_column
      config: { flex: 1 }          # right column: 1 share
      children:
        - component: chat_panel
```

### Regions own padding, scroll, and spacing

A region automatically applies token padding around its content, the scroll
boundary, and the gap between stacked siblings. You **don't** set `overflow` or
`padding` by hand — drop a layout primitive into a region and it fills the area;
the region handles the chrome.

Spacing comes from theme tokens; override the gap per-region with
`config: { gap }`:

| Token | Default | Used for |
|---|---|---|
| `--spacing-panel` | `16px` | region / panel / modal-body padding |
| `--spacing-gap` | `16px` | default gap between stacked siblings |
| `--spacing-gap-tight` | `8px` | dense gap — button groups, metric rows, footers |

### Responsive behaviour

The engine reflows automatically — **author desktop intent only**.

- **Auto-stack below ~600px.** A horizontal block of columns collapses to a
  full-width vertical stack, and a row's children stack, below a 600px viewport.
- **Modals go full-screen below ~600px** (full width/height, no border-radius).
- **Charts, graphs, and tables observe their own container** and redraw correctly
  inside collapsible panels, tabs, and modals on resize.
- **Long text** truncates or wraps; a component never pushes a horizontal
  scrollbar out of its card.

## Components reference

Every component below is rendered from the registered library. Layout primitives
nest other components via `children:`; data and form components bind via `data:`
and read fields via `config:`.

### Layout & containers

| Component | What it does |
|---|---|
| `layout_row` | Lay children out side by side. |
| `layout_column` | Stack children top to bottom. |
| `card` | A titled container; its body is a region (axis-inferred, padded, scrollable). |
| `tabs` + `tab` | A tabbed surface (`slot: tab` markers paired with `slot: panel` content). |
| `button_group` | A row of buttons with consistent spacing. |
| `modal` | An overlay with `body:` / `footer:` content. See [Modals](#modals-and-detail-views). |

### Data display

| Component | What it does |
|---|---|
| `table` | Paginated, sortable table with columns, row actions, and click-to-detail. |
| `metrics` | A strip of labelled KPI tiles, each reading one `field` with a `format`. |
| `detail_list` | A read-only label→value record (definition list). |
| `markdown` | Render a markdown field. |
| `tag_list` | Render a string-array field as chips. |
| `status_indicator` | A coloured dot + label for a status field. |
| `container_status` | Lifecycle summary + start/stop/restart controls for a factory's containers. |
| `line_chart` | Line / area / multi-series chart. |
| `bar_chart` | Grouped or stacked bar chart. |
| `treemap` | Hierarchical treemap. |
| `scatter` | 2D X/Y scatter with optional quadrants and click-to-open. |
| `force_directed` | Force-graph for state machines / agent diagrams. |
| `code_editor` | Monaco editor bound to a code/text field. |
| `tree_editor` | Edit a flat collection as a tree (parent-pointer rows). |
| `file_explorer` | Browse / upload / delete files in a factory **volume**. |

### Forms & inputs

| Component | What it does |
|---|---|
| `text_input` | Single-line input (`type: text|email|password|number`). |
| `textarea` | Multi-line input. |
| `select` | Single-choice dropdown. |
| `multi_select` | Multi-choice combo bound to an array field. |
| `button` | A button that dispatches an action. |
| `button_group` | A laid-out set of buttons. |

### Chat & status

| Component | What it does |
|---|---|
| `chat_panel` | Inline chat connected to the factory's LLM agent (factory pages only). |
| `spinner` / `empty_state` / `error_state` | Theme-aware loading / empty / error placeholders (usually handled for you). |

### Config examples for the key components

#### `table`

```yaml
component: table
data:
  collection: invoices
  state: pending             # optional — channel filter
config:
  key_field: invoice_id      # unique row key
  page_size: 50
  sort_field: fetched_at     # default sort (a data key or _updated_at/_state/_key/_created_at)
  sort_dir: desc
  columns:
    - { field: invoice_id,    label: "Invoice #" }
    - { field: vendor.name,   label: Vendor, truncate: 30 }   # nested dot-path
    - { field: title,         label: Title, link_field: url } # cell becomes a link
    - { field: total_amount,  label: Amount, format: number }
    - { field: classified_pct, label: Classified, format: percentage }
    - { field: fetched_at,    label: Fetched, format: relative_time }
    - { field: is_paid,       label: Paid, kind: bool }       # ✓ / ✗ icon
```

Column headers are clickable to sort, and sorting is applied server-side across
the whole collection (not just the visible page). See [Actions](#actions) for
`row_actions` and `on_row_click`.

#### `metrics`

```yaml
component: metrics
title: Summary
data: { collection: kpis, state: latest, latest: true }
config:
  direction: horizontal
  metrics:
    - { field: prospects_monitored, label: Prospects, icon: users, format: number }
    - { field: pipeline_value,      label: Pipeline,  icon: dollar-sign, format: currency }
    - { field: classified_pct,      label: Classified, format: percentage }
```

#### `detail_list`

A clean read-only record. With no `data:` block it reads from the surrounding
context — ideal inside a modal showing a clicked row's fields.

```yaml
component: detail_list
config:
  fields:
    - { field: row.company_name,   label: Company }
    - { field: row.pipeline_value, label: Pipeline value, format: currency }
    - { field: row.updated_at,     label: Last updated, format: relative_time }
```

#### Charts

```yaml
# line_chart — over a "latest snapshot" row holding an array.
component: line_chart
data: { collection: stats, state: chart_data, latest: true }
config:
  title: "$: active_chart_label"   # optional heading; $: resolves against the bound row
  data_field: daily                # field within the row holding the array
  x_field: day
  y_field: amount
  series_field: category           # optional — one line per distinct value
```

```yaml
# bar_chart — grouped (default) or stacked.
component: bar_chart
data: { collection: events }
config:
  group_by: source
  x_field: source
  y_field: count
  stacked: false                   # true to stack series within each x group
```

`line_chart`, `bar_chart`, and `treemap` all accept a `title:`. Chart fills come
from a named colour `scale` — don't hardcode bar/line colours; pass a factory
`color_map` only when you need a fixed category mapping.

#### Form inputs

```yaml
component: text_input
config: { field: name, label: Name, placeholder: Enter name, required: true }
```

```yaml
component: textarea
config: { field: notes, label: Notes, rows: 4 }
```

```yaml
component: select
config:
  field: status
  label: Status
  options:
    - { value: open,   label: Open }
    - { value: closed, label: Closed }
```

```yaml
component: button
config: { label: Generate, variant: primary, icon: plus }   # variant: primary|secondary|danger|ghost
on_click:
  action: save_data_item
  collection: jobs
  key: $uuid
  state: requested
```

### The `format:` vocabulary

`format` controls cell-level rendering on `metrics`, `detail_list`, read-only
inputs, and `table` columns:

| Value | Output |
|---|---|
| `number` | Locale-grouped (e.g. `1,234`). |
| `percentage` | `XX%`. |
| `currency` | Compact AUD (e.g. `$4.5M`, `$1.2K`, `$950`). |
| `duration` | `123ms`. |
| `relative_time` | `just now` / `5m ago` / `2h ago` / `3d ago`. |

## Data binding

The `data:` block tells a component where to read from. It has a few modes; the
component picks the transport (live push vs. one-shot fetch) for you.

```yaml
# Mode 1 — full collection. rows = every row; live-updates on change.
data: { collection: documents }

# Mode 2 — collection filtered to one or more states.
data: { collection: documents, state: drafted }
data: { collection: documents, state: [drafted, approved] }   # IN list

# Mode 3 — latest single row from a (collection, state) channel.
data: { collection: stats, state: pipeline_stats, latest: true }

# Mode 4 — inline / static (no fetch).
data:
  inline:
    - { id: 1, label: A }
    - { id: 2, label: B }
```

- **`collection`** — a `factory_data` collection name.
- **`state`** — narrows to a lifecycle state (a string, or an array for an IN
  list). This selects the data channel.
- **`latest: true`** — read only the most recent row of the channel (a snapshot
  produced by an aggregator agent). The component sees a 0- or 1-row set.

A component bound to a collection automatically subscribes to live updates and
re-fetches when the data changes — no wiring needed.

### Reading a `field`

Within a bound row, components read individual values by `field:` — a
dot-notation path (`vendor.name`, `context_brief.tone_guidance`).

### Dynamic values with `$:`

Anywhere a config value should be *derived* (a label, a modal title, an action's
`key`), prefix a string with `$:` to evaluate it as an expression against the
current row:

```yaml
title: "$: 'Detail ' & invoice_id"
key:   "$: invoice_id"
label: "$: touch_count >= 3 ? 'Exhausted' : 'Active'"
```

!!! warning "Use bare field names — not `$.field`"
    Reference fields by their bare name (`invoice_id`), not with a leading `$.`.
    `"$: $.invoice_id"` silently evaluates to nothing. Write `"$: invoice_id"`.

Plain (non-`$:`) strings, numbers, and booleans are always literal.

### `filter:` on top of `data:`

`data.state` selects a *channel*; `filter:` winnows *rows* on top of it without
changing the subscription. Two forms:

```yaml
# Object form — equality, IN-list, and range ops.
filter:
  state: drafted
  priority: [high, medium]
  score: { gte: 0.5 }

# Expression form — an arbitrary per-row predicate.
filter: "$: touch_count >= 3"
```

## Actions

Interactive surfaces — buttons, `table` row actions, chart/graph clicks — emit
through top-level event handler keys (`on_click`, `on_change`, `on_row_click`,
`on_point_click`, `on_node_click`, `on_blur`, …). Each handler names an `action:`
with its parameters as **flat siblings** (no `args:` nest).

### The canonical action set

| Action | Effect | Params |
|---|---|---|
| `save_data_item` | Write a row: `PUT /…/data/{collection}/{key}` with `{ data?, state? }`. Used for form saves, state transitions, and triggering agents. | `collection`, `key` (required); `state`, `data_field`, `data` (optional) |
| `delete_data_item` | Delete the row at `{collection}/{key}`. | `collection`, `key` |
| `open` | Activate a registered sibling by `id:` (a modal, panel…). | `id`; `subject` (optional) |
| `close` | Dismiss the nearest open host (or one by `id:`). | `id` (optional) |

!!! note "Modifiers"
    Add `then_close: true` to any action to close the host after it dispatches
    (e.g. save-and-close). Set `key: $uuid` to write a **fresh row each click** —
    the canonical pattern for triggering an agent: each click drops a new row in
    the agent's input collection at its input state.

### Worked examples

A **"Generate" button** that triggers an agent by writing a fresh row:

```yaml
- component: button
  config: { label: Generate next day, variant: primary, icon: forward }
  on_click:
    action: save_data_item
    collection: plan          # the agent's input collection
    key: $uuid                # a brand-new row each click
    state: requested          # the input state the agent subscribes to
```

A **row action** — an Approve button on each table row (a pure state
transition; the existing row value is preserved):

```yaml
config:
  row_actions:
    - icon: check
      action: save_data_item
      collection: review_queue
      key: "$: invoice_id"    # reads the row's field
      state: approved
    - icon: eye
      action: open            # open a detail modal by id
      id: invoice_detail_modal
```

An **edit modal** with Save and Delete in the footer:

```yaml
- component: modal
  id: invoice_detail_modal
  title: "$: 'Invoice ' & row.invoice_id"
  body:
    - component: textarea
      config: { field: row.notes, label: Notes }
  footer:
    - component: button
      config: { label: Delete, variant: danger, icon: trash }
      on_click:
        action: delete_data_item
        collection: invoices
        key: "$: row.invoice_id"
        then_close: true
    - component: button
      config: { label: Save, variant: primary, icon: floppy-disk }
      on_click:
        action: save_data_item
        collection: invoices
        key: "$: row.invoice_id"
        then_close: true     # a footer Save button auto-attaches the edited row
```

!!! note "Footer Save buttons attach the whole edited row"
    A `button` Save in a modal footer automatically sends every edit made by the
    inputs in the modal body — no `data:` block needed. Use `data_field: <path>`
    to save just one slice instead.

## Modals and detail views

A `modal` accepts `body:` and `footer:` keys carrying child configs directly —
no `slot:` wrapper needed. The body is a region (axis-inferred, padded,
scrollable):

```yaml
- component: modal
  title: Edit order
  config: { width: 720px, max_height: 80vh }
  body:
    - component: detail_list
      config:
        fields:
          - { field: customer, label: Customer }
          - { field: total, label: Total, format: currency }
  footer:
    - component: button
      config: { label: Close, variant: secondary }
      on_click: { action: close }
```

Modals get keyboard shortcuts for free: `Escape` closes, and `⌘/Ctrl+S` fires
the primary footer button.

### Click a table row to open a detail modal

The recommended pattern: register a `modal` with an `id:` somewhere in the tree,
then point the table's `on_row_click` at it. The clicked row is published so the
modal's children resolve `$: row.field`:

```yaml
- component: table
  data: { collection: invoices }
  config: { key_field: invoice_id, columns: [ ... ] }
  on_row_click:
    open: invoice_detail_modal      # id of a sibling modal

- component: modal
  id: invoice_detail_modal
  title: "$: 'Invoice ' & row.invoice_id"
  body:
    - component: detail_list
      config:
        fields:
          - { field: row.vendor.name, label: Vendor }
          - { field: row.total_amount, label: Amount, format: currency }
  footer:
    - component: button
      config: { label: Close, variant: secondary }
      on_click: { action: close }
```

!!! note "`open:` takes a string id only"
    Click-to-open targets reference a registered sibling by its `id:` — a literal
    string (`open: invoice_detail_modal`) or a `$:` expression yielding one. The
    target modal stays dormant until triggered.

### Read-only records

For a static, read-only view of a record, use `detail_list` — it renders a clean
label→value definition list with per-field `format:`, no input chrome. It's the
right tool for showing a clicked row's fields inside a modal or panel (use it
instead of stacking disabled `text_input`s).
