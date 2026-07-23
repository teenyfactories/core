# ui-common — universal keys, vocabulary, layout, data binding, JSONata, actions

Factory dashboards, factory-editor pages, and orchestrator settings are built from composable YAML under `default_ui.layout`. Components nest recursively. Every spec is validated server-side against a strict Zod schema; unknown/legacy keys and bad shapes are rejected at chat-edit time and factory-load time — no tolerant mode.

Per-leaf component config lives in its own doc — read it via `read_docs` as `ui-<component>` (e.g. `ui-table`, `ui-scatter`). This doc covers only what's universal across every leaf.

## Universal top-level keys

Every component block shares the same top-level keys. `ComponentRenderer` (the dispatcher) reads only these; everything else lives under `config:`.

```yaml
component: <leaf>          # REQUIRED — leaf type, snake_case
id: <stable_id>            # Optional — cross-component reference
title: "Display Title"     # Optional shorthand for layout-with-header leaves (card, modal, tabs, metrics)
data: {...}                # Optional — data binding (6 modes; see Data binding)
filter: {...}              # Optional — row-level winnowing, object form or JSONata string
transform: "$:..."         # Optional — JSONata expression: rows → rows
on_click: {...}            # Event handlers, TOP-LEVEL, FLAT sibling params on `action:` (no `args:` nest, no
on_change/on_select/...    #   `actions:` wrapper). Also on_row_click/on_node_click/on_point_click/on_search/
on_blur: ...               #   on_submit/on_toggle. Array form: on_click: [ { action: A }, { action: B } ]
preset: <name>             # Optional — named base config; `config:` overrides selectively
overlays: [ { type: text|line|rect|arrow|image|point|band, ... } ]   # Optional — chart/viz annotations
config: {...}              # Per-leaf configuration — see the leaf's own ui-<component> doc
children: [ ... ]          # Optional — nested children (layout components only)
slot:                      # Optional on a CHILD — names its slot (only inside slot-aware parents)
style:                     # Optional — CSS-style object for THIS component's box; top-level only, never `config:`
show_when: "$:..."         # Optional — JSONata expression; component renders only when truthy.
```

**No legacy keys.** The strict gate rejects: `type:` (use `component:`), `data_collection:`/`topic_full_data:`/`topic_incremental_data:`/`inline_data:`/`update_topic:` (use `data:`), `config.style:`/`config.data:` (lift to top-level `style:`/`data:`), `Table.filter_by:`/`ForceDirected.node_filter:` (use top-level `filter:`), `chart_type:` (split into `line_chart`/`bar_chart`/`treemap`/`scatter`), `action: publish`/`store`/`apply`/`delete`/`save_and_restart` (see Actions), `{{...}}` templates and bare-jq expressions (use `$:` JSONata).

**Event-handler shape.** Canonical is a top-level `on_<event>:` key with **flat sibling params** on `action:`. A legacy `actions: { on_click: { action, args: {...} } }` shim still normalizes at render time with a `console.warn` — do not author new YAML in that shape. `style:` is a top-level key on every component, consumed as a CSS-style object; nesting it under `config:` is silently ignored.

## Canonical config vocabulary

Every component exposes config in **one vocabulary** regardless of the rendering library underneath — the same `x_field`/`label`/`format`/`sort_dir` whether the leaf is recharts, d3, Monaco, FontAwesome, or raw HTML. Library-native prop names (recharts `dataKey`, d3 `nodeId`/`chargeStrength`, Monaco `wordWrap`/`vs-dark`, FontAwesome `fas`, HTML `type="email"`, MUI/antd terms) MUST NOT appear in factory.yml — a needed library feature gets a wrapper key here first (closed-extension).

### Field references (`*_field`)

Dot-paths accepted (`user.profile.tier`).

| Key | Role |
|---|---|
| `field` | Single field a leaf binds to (form inputs, displays). |
| `data_field` | Field on `rows[0].data` holding the chart/scatter payload array in `latest: true` mode. |
| `key_field` | Unique row identifier (Table, anything paginated). |
| `x_field`, `y_field` | Axis fields for 2D plots. |
| `series_field` | Groups rows into series (line_chart, bar_chart). |
| `name_field`, `value_field` | Hierarchical/KV plots (treemap). |
| `label_field` | Display-label field (StatusIndicator, ScatterPlot tooltip). |
| `color_field` | Categorical colour-map field (ScatterPlot). |
| `link_field` | URL a Table cell wraps. |
| `id_field`, `parent_field`, `order_field` | Tree shape (TreeEditor — flat collection, `parent_id` pointer). |
| `tooltip_fields` | **Array** of field names in a tooltip (plural — many values). |

One concept → one key across leaves (always `key_field`, never `pk` elsewhere). Component-specific roles end `_field`; arrays of field names are plural.

### Display labels

`title` — header/page-title text on layout-with-header leaves (`card`, `modal`, `tabs`, `metrics`, `empty_state`, `error_state`). `label` — field/button-level text on form leaves, columns, metrics, row_actions. `placeholder` — greyed hint in an empty input (form leaves). `hint` — help text via the `?` info icon (form leaves). `description` — body text under the title (`empty_state`, `error_state`). `empty_text` — inline placeholder for zero items (`tag_list`). No synonyms (`caption`, `name`, `text`, `subtitle`, `tooltip` are not canonical).

### Sort / ordering

`sort_field` — a data key, or a row-meta field (`_updated_at`, `_created_at`, `_state`, `_key`). `sort_dir` — `"asc"`|`"desc"`, never `ascending`/`1`/`-1`. `sortable` — per-column `bool` (Table only), `false` disables header-click sort, default `true`. `sort_field`/`sort_dir` set the default **server-side sort** across the whole collection (before pagination) for a data-bound Table; clicking a header overrides at runtime. Omit both → backend default `_updated_at desc`.

### Sizing and pagination

| Key | Type | Where |
|---|---|---|
| `page_size` | int | Rows per page. |
| `max_rows` | int | Hard cap on rendered rows (Table). |
| `max_height` | CSS length | Max height of a scrollable body (Markdown). |
| `max_width`, `max_height`, `width`, `height`, `min_height` | CSS length | Modal sizing only — most leaves fill their flex slot. `min_height` floors tabbed/variable-content modals. |
| `rows` | int | Textarea visible rows. |
| `min_*` | matching units | Lower bounds (rare). |
| `truncate` | int | Max characters before an ellipsis (Table cells). Integer only. |

Avoid `limit` (ambiguous), `count` (aggregations), `pageSize` (camelCase), `truncate: true`, `ellipsis:`/`clamp:`.

### Format / display

`format` is canonical for **cell-level rendering**: `"number"` (locale-grouped int or 2-decimal float), `"percentage"` (`0–1` floats → `XX.X%`; >1 assumed already percent), `"relative_time"` (`"just now"`/`"5m ago"`/`"2h ago"`/`"3d ago"`), `"duration"` (`"123ms"`, Metrics). Used by `metrics`, `detail_list`, read-only `text_input`/`textarea` via shared `utils/formatValue`; `table` keeps its own cell formatter (slightly different `relative_time`/boolean handling) deliberately. New values land here first, then in `formatValue`.

### State / behaviour flags

| Prefix | Intent | Examples |
|---|---|---|
| `show_*` | Visibility of an internal sub-part. | `show_dot`, `show_label`, `show_controls`, `show_summary`, `show_grid`, `show_arrows` |
| `allow_*` | Permission for an interaction. | `allow_selection` |
| `auto_*` | Automatic opt-in behaviour. | (reserved — none today) |
| `read_only`, `required`, `disabled`, `loading` | Standard form/async states — the ONLY unprefixed booleans. | Form leaves, Button. |

Every other component-defined boolean carries a prefix. Avoid `enable_*`, `is_*`, `*_enabled`.

### Colour and scale

`scale: { scheme: <name> }` — named colour scale for chart/categorical series (`bar_chart`/`line_chart`/`treemap` fills, `scatter` `color_field` dots; default `theme_categorical`). `color_map: { <category>: <hex|css-var> }` — per-category override of the auto-palette (ScatterPlot); unlisted categories still get a scale colour. `default_color` — fallback for a null/missing category value, not for "unmapped" ones. `color:` (direct string) is valid only inside `style:` or a JSONata-resolved style value, never a top-level config key. Chart fills come from `scale.scheme`; chrome comes from `theme.*`/`var(--*)`. Raw `#hex` only inside `color_map` or a factory-passed style.

### Icon references

FontAwesome icon names only (`icon: chart-line`, never `icon: fas:chart-line` or an object shape). `icon_position: "left" | "right"` for buttons. If a second icon source is adopted, `icon:` becomes a prefixed enum (`material:settings`); until then bare names are FA.

### Action params

Every event handler is a top-level `on_<event>` key; params are flat siblings of `action:`, never under `args:`. Closed 6-entry enum (`save_data_item`, `delete_data_item`, `open`, `close`, `set_url_param`, `custom:<name>`) — see Actions below.

### Slot names

Slot-aware parents (Tabs, Modal) define allowed child slots. Canonical: `tab`, `panel`, `header`, `body`, `footer`.

### Naming style and rules of thumb for a new component

snake_case in YAML (`x_field`, `page_size`, `read_only`); PascalCase for JS component/export names; registry type names snake_case. No abbreviations (`label` not `lbl`) except `dir` in `sort_dir`. No mixed numeric/string enums (`sort_dir: "asc"`, not `1`).

1. Concept already named? Use the existing key.
2. Library-native name? Wrap it (Monaco `wordWrap`→`word_wrap`; recharts `dataKey`→`x_field`/`y_field`/`value_field`; d3-force `chargeStrength`→`charge_strength`, acceptable since the physics concept *is* the role).
3. Library-native value? Wrap/enumerate (Monaco `theme: "vs-dark"`→`theme: "dark"|"light"`; HTML `type:` enums leak — prefer purpose-named leaves).
4. Boolean? Prefix (`show_*`/`allow_*`) unless a form-state quartet member.
5. Many of something? Pluralise. 6. Field reference? Suffix `_field`. 7. Document it here first — a new vocabulary key is a doc edit before a JS edit.

### Canonical keys (current)

These leaf-specific keys are canonical as written, no alternate spellings exist: `color_map`; `mode: "compact"|"default"` (`container_status`); `show_close_button` / `show_quadrants` (`modal` / `scatter`); `edge_distance`/`edge_strength`/`edge_bundling`/`edge_bundling_offset`/`edge_bundling_min_offset`/`edge_anchor_center` (`force_directed` — d3-internal names stay `link*`); row-click detail via top-level `on_row_click.detail_modal` (`table`); fire-and-forget signals via array-form `on_click` writing a domain collection with `key: $uuid`; flat-collection `tree_editor` with a `parent_id` field, internal `save_data_item`/`delete_data_item` dispatch. Also fixed by design: `code_editor.theme: "vs-dark"|"light"`; Table column `type: "tags"` (scope-polymorphic `type:` at column level is fine — only the top-level `type:` was replaced by `component:`); `text_input.type: "email"|"password"|"number"|"text"`.

## Layout & responsive

`layout_row`/`layout_column` size their children, own padding/scroll/gap, and adapt to mobile automatically. Most layouts need no `style:` blocks; reach for `style:` only when the primitives can't express what you need (it always wins, spread last).

### The two primitives

`layout_row` — horizontal flex row, axis **fixed** (row), never inferred; places children side by side. `layout_column` — vertical flex column, axis **fixed** (column); stacks children top-to-bottom. Both take `config: { gap, align, justify }` (`gap` defaults to `--spacing-gap`, `align`→`stretch`, `justify`→`flex-start`); children take `config: { flex: N }` (see sizing below). Never hand-write `flex`/`display`/`flex-direction` on `style:`.

### Axis inference (regions)

A **region** (modal body, tab panel, card body) has no fixed axis — it infers from its children: containing `layout_column` siblings ⇒ horizontal (columns sit side by side); containing `layout_row` siblings ⇒ vertical (rows stack); only leaves (no layout primitives) ⇒ vertical (leaves stack); exactly one child ⇒ vertical, the child fills the region. Rationale: a block *containing* `layout_column`s goes horizontal because each column is vertical internally, so columns belong side by side — pick the child primitive by how it should lay out internally, and the parent axis follows. `layout_row`/`layout_column` are always explicit, never inferred.

### `no-mixed-axis` (hard constraint)

A region must not contain both a `layout_row` and a `layout_column` sibling — there's no single correct axis. At runtime the host (Card/Modal/Tabs) logs `console.error` and renders a red "Layout error" banner, falling back to a vertical stack. At discovery/editor time the backend validator (`schemas/validate.js`) emits a `no-mixed-axis` issue at the offending path. (Doesn't apply to `tabs`' flat children — `slot: tab`/`slot: panel` distribute into separate panel regions.) Fix: nest one inside the other — wrap `layout_row` siblings in an outer `layout_column` (or vice versa):

```yaml
# WRONG: card children = [layout_row, layout_column]  ✗ mixed siblings
# RIGHT: card children = [layout_column { children: [layout_row, layout_row] }]
- component: card
  children:
    - component: layout_column      # one region: a column of rows ⇒ vertical
      children:
        - component: layout_row
          children: [ ... ]
        - component: layout_row
          children: [ ... ]
```

### `redundant-singleton` (check_ui warning)

A layout primitive earns its keep by laying **multiple** children out. `check_ui` (`schemas/validate.js`) flags a wrapper that has nothing to lay out:

- a **`layout_column` / `layout_row` with exactly one in-flow child** — it adds no layout but still imposes its own flex defaults on that child (`flex: 1 1 0; align-items: stretch`), so the child stretches to the wrapper instead of sitting naturally in the parent slot. Fix: promote the single child into the wrapper's slot (delete the wrapper). Out-of-flow children — `modal`, `commit_modal`, `confirm_destructive_modal` (React-portaled, not flex siblings) — **don't count**, so `layout_column` children `[tabs, modal]` is still a singleton (one in-flow child). Applies to the **root** too. If the wrapper exists only to co-host a modal beside one real child, move the modal to the top-level **`default_ui.modals`** array (see `ui-modal`) and delete the wrapper.
- a **`tabs` with exactly one `slot: tab` / `slot: panel` pair** — a one-tab strip has nothing to switch to; render the panel's content directly.

**Suppressed** (left alone) when the wrapper carries meaningful differentiating config that positions/sizes the child: `config.justify`, `config.align`, `config.flex`, or a top-level `style:` — e.g. a `layout_column { config: { justify: center } }` that exists purely to centre its child is legitimate. `config.gap` does **not** suppress it (gap only spaces siblings, so it's inert on a lone child).

```yaml
# WRONG: a column wrapping a single table adds nothing
- component: layout_column
  children:
    - { component: table, data: { collection: rows } }
# RIGHT: use the table directly in the parent slot
- { component: table, data: { collection: rows } }
```

### `misplaced-under-config` (check_ui error)

Some keys are read from the **component node**, never from `config:`. Nesting one inside `config:` is a silent dud — no runtime error, the content just never mounts or the handler never fires. `check_ui` (`schemas/validate.js`) now rejects them so `edit_ui` refuses the save instead of shipping a dead UI:

- **`children:` / `data:` / `component:`** under `config:` → the subtree never mounts (observed: a `detail_modal` whose inputs were nested under `config:` → dead fields, no error).
- **`on_row_click:`** under `config:` → the whole table's row-click is inert: no pointer cursor, no detail modal, no error (observed in the wild: a factory whose entire opportunity/programme tables were unclickable). `on_row_click` is a **top-level sibling of `config:`** — unlike `row_actions:`, which correctly lives **inside** `config:`. Don't mirror `row_actions`' placement.

```yaml
# WRONG: on_row_click buried in config → rows silently unclickable
- component: table
  config:
    columns: [ { field: name, label: Name } ]
    on_row_click: { open: detail_modal }
# RIGHT: on_row_click is a sibling of config
- component: table
  config:
    columns: [ { field: name, label: Name } ]
  on_row_click: { open: detail_modal }
```

### Child sizing — `config.flex`

Omitted, in a `layout_row`: grow/split width equally (`flex: 1 1 0`) — why a row of columns splits width with no per-child config. Omitted, in a `layout_column`: content height (`flex: 0 0 auto`), so a column of form fields isn't squished — opt one child into filling with `config: { flex: 1 }`. `flex: N` (N>0): N shares of leftover space along the main axis AND fills the cross axis (a card/chart/table given `flex: 1` fills its whole box; larger N = larger share). `flex: 0`: size to content, no grow, no cross-axis fill — header strips, `metrics` rows, `button_group`.

```yaml
# A column: a metrics strip (content height) above a chart that fills the rest.
- component: layout_column
  children:
    - { component: metrics, config: { flex: 0 }, data: { collection: kpis, latest: true } }
    - component: card
      config: { flex: 1 }          # fills the remaining height AND width
      children: [ { component: line_chart, data: { collection: revenue } } ]
```

Gotcha: a no-`flex` child in a `layout_column` sits at content height — a chart/table/card you want to *fill* the column needs `config: { flex: 1 }` explicitly. A `layout_row` of two `layout_column`s (each its own `config: { flex: N }` share) builds a full multi-column dashboard with no `style:` anywhere.

### Regions own padding, scroll, gap

Automatically, from spacing tokens: token padding (`--spacing-panel`, `16px`) insetting content, scroll boundary (`auto` for multi-child stacks, `hidden` for a single self-scrolling leaf like table/chart), and token gap (`--spacing-gap`, `16px`) between siblings (override with `config: { gap }`; tight variant `--spacing-gap-tight`, `8px`, for button groups/metric rows/modal footers). Modal min-height floor `--spacing-modal-min`, `480px`. Don't hand-set `overflow`/`padding`, and don't wrap body content in a `grid` just for spacing.

### Responsive behaviour

Engine reflows automatically — author desktop intent only. **Auto-stack below ~600px** viewport: a horizontal block of `layout_column` siblings, or a `layout_row`'s children, collapse to a full-width vertical stack, via a viewport `@media (max-width: 600px)` rule on the region/row (not `@container` — stacking keys off viewport width, not the row's own box). **Modals go full-screen below ~600px** (full width/height, no border-radius). **Measurement-driven leaves** (charts, force-directed graphs, canvas, tables) observe their own container via `ResizeObserver`, never `window`. **Long text** truncates or wraps; no component pushes a horizontal scrollbar out of its card. No `container-type`/CSS containment is set anywhere.

### Modal `body:` / `footer:` named keys

A modal accepts `body:`/`footer:` as named keys carrying child configs directly (single node or array) — no `slot:` wrapper needed. The body is a region (axis-inferred, scroll, token padding). Legacy `slot: body`/`slot: footer`/`slot: header` still works and may be mixed with named keys.

```yaml
- component: modal
  title: Edit order
  body:
    - component: detail_list
      config: { fields: [ { field: customer, label: Customer }, { field: total, label: Total, format: currency } ] }
  footer:
    - component: button
      title: Save
      on_click: { action: save_data_item, collection: orders, key: "$: row.key", data_field: row }
```

### `grid` is LEGACY

`grid` is deprecated — use `layout_row`/`layout_column`. Still renders for existing factories; do not author new `grid` blocks. Migrating: `direction: horizontal`→`layout_row`, `direction: vertical`→`layout_column`, per-child `style: { flex: N }`→`config: { flex: N }`, drop hand-wired padding/overflow/gap (the region owns it). `style: { height: ... }` on chart rows, `flexShrink: 0`/`minWidth: 0`/`overflow: hidden` workarounds are anti-patterns — the primitives size correctly without them.

## Data binding (the `data:` block)

Six modes. The hook (`useBoundData`) chooses transport (REST GET / SSE push / poll-fallback / static) from the spec; components never branch on transport. Returns `{ rows, loading, error, lastUpdated, refetch, notFound, truncated, urlKey }`; `rows` is always an array (`latest: true` and `key_from_url` produce a 0- or 1-element array).

**Bounded, not unbounded.** A collection-list binding (Mode 1/2, not `latest: true`) fetches the whole collection in one request up to a **hard cap of 10,000 rows**; paginating leaves (e.g. `table`) client-paginate over that full bounded set. Beyond the cap the result clips and the hook sets `truncated: true` (`table` shows a "showing the first N rows" notice). `latest: true` is exempt. A collection that routinely exceeds the cap wants server-side filtering (`state:`) or a purpose-built streaming view.

**Transport: push-first via SSE.** One `EventSource` per tab connects at `/api/events` (server mints a `sessionId`); subscriptions add via `POST /api/events/:sessionId/subscribe` (`useEvents.js` client / `eventBus.js` server). `useBoundData` rides on top for collection modes — while SSE is connected and the tab visible, freshness is push-driven and polling is suspended.

### Mode 1 — full collection

```yaml
data:
  collection: documents          # rows = every row in the collection
```

Subscribes to SSE `tf.{factory}.{collection}.changed`, re-fetches on each event.

### Mode 2 — collection filtered to state(s)

```yaml
data:
  collection: documents
  state: drafted                 # single state — string
# OR
data:
  collection: documents
  state: [drafted, approved]     # multi-state — IN list
```

Server applies the filter; subscription tracks `_changed`.

### Mode 3 — latest single row from a (collection, state) channel

```yaml
data:
  collection: stats
  state: pipeline_stats
  latest: true                   # rows[] is 0 or 1 — most recent only
```

The canonical "latest-of-state" mode — an agent writes at the named state; the UI reads the latest one (`metrics`, `markdown`, `scatter`). Producer: `tf.collection('<domain>').set('<key>', state='<topic>', data=…)`, or `tf.on_schedule.every(N).<unit>.do(…)` for scheduled stats.

### Mode 4 — inline / static

```yaml
data:
  inline:
    - { id: 1, label: A }
    - { id: 2, label: B }
```

No fetch, no subscribe — demos and static pickers.

### Mode 5 — arbitrary HTTP endpoint

```yaml
data:
  endpoint: /api/usage/summary
  poll: 30                       # seconds; 0 = one-shot; absent = default
```

REST GET against an arbitrary endpoint — orchestrator-level pages (settings, usage, admin).

### Mode 6 — single row keyed by a URL query param (`key_from_url`)

```yaml
data:
  collection: wiki
  key_from_url: wiki_select      # row key = value of the ?wiki_select=… URL param
```

READ half of the symmetric pair with the `set_url_param` action (see Actions). Reads the URL param, fetches the single row at `GET /api/factories/{factory}/data/{collection}/{key}`, subscribes to `.changed` so live edits push through.

- No param value → no fetch, `rows` empty, `notFound: false`.
- Param present but no such row (stale bookmark) → `rows` empty, `notFound: true`, `urlKey` set to the param value so the component can render a bespoke message.
- Re-fetches when the param changes (a `set_url_param` write, a `[[slug]]` wiki-link click, or Back/Forward).

Per-tab and bookmarkable — selection lives in the tab's URL, not a shared DB row. Requires `collection`; pairs with `set_url_param` on a sibling selector.

### Polling (fallback only)

Push via SSE is primary. The poll loop runs only when SSE is disconnected, or the tab is hidden (`document.visibilityState === 'hidden'`); when SSE reconnects and the tab is visible, polling suspends. `poll: 0` — one-shot fetch, no fallback timer. `poll: <N>` — fallback poll every N seconds when push unavailable (for `endpoint:` mode, with no push transport, this is the active interval). Absent — default fallback 5s when push unavailable, overridable via `COMPOSABLE_UI_DEFAULT_POLLING_SECONDS`.

### Push lifecycle (tab visibility)

visible→hidden: unsubscribe from the SSE topic, stop the poll loop. hidden→visible: immediate refetch (backfills anything missed), re-subscribe. No per-component IntersectionObserver — long dashboards keep receiving pushes for offscreen cards while the tab is foregrounded (locked deferred decision).

### Topic shape (for custom subscribers)

`useBoundData` derives topics for you; custom components using `useEvents` directly need: `tf.{factory}.{collection}.changed` (every write — what `useBoundData` consumes), `tf.{factory}.{collection}.{state}.{op}` (state-and-op-specific, `op`: insert|update|delete, not consumed by `useBoundData`), `chat.{requestId}` (chat streaming events).

**Large-payload pattern.** SSE frames carry metadata only, never the full row `value`. When `_size_hint === 'large'`, consumers fetch the body via `GET /api/factories/:name/data/:collection?since=<ts>`. `useBoundData` handles this transparently; custom subscribers must do it themselves.

### `filter:` is on top of `data:`

`data.state` is a CHANNEL selector (which subscription/server-side query); `filter:` is row-level winnowing applied on top, without changing subscription scope. Two forms, both supported at runtime on every data-bound component:

```yaml
# Object form — each value resolved against the parent DataRef scope at render.
- component: table
  data: { collection: messages }
  filter:
    prospect_id: "$: prospect_id"   # parent row's id (e.g. nested in a detail_modal)
    state: drafted                  # plain literal — equality
    priority: [high, medium]        # array — IN
    score: { gte: 0.5 }             # range op

# JSONata-string form — arbitrary per-row predicate.
- component: table
  data: { collection: prospects }
  filter: "$: touch_count >= 3"
```

Object-form values can be literals or `$:` expressions resolved against the PARENT context; string form evaluates per-row with the row as scope. When the source supports pushdown, object-form filters translate to server-side WHERE clauses; JSONata strings always run client-side.

## Slot rendering

Some parents group children into named slots via the child's top-level `slot:`. `modal`/`card` allow `header`/`body`/`footer` (default `body` for children with no `slot:`); `tabs` allows `tab`/`panel` (no default — every child MUST declare `slot:`). A `slot:` value outside the parent's allowlist is rejected; children under a non-slot-aware parent MUST NOT carry `slot:`.

```yaml
component: modal
title: Detail
children:
  - { slot: body, component: tabs, children: [ ... ] }
  - slot: footer
    component: button_group
    children:
      - { component: button, config: { label: Close }, on_click: { action: close } }
```

## JSONata for dynamic values

Any config taking a derived value (cell labels, formatted strings, computed styles, action params, conditional visibility) opts into JSONata via the `$:` prefix on a string.

Default is literal: `label: "Email Drafter"` → literal; numbers/booleans/arrays → literal. JSONata via `$:` prefix: `label: "$:value.subject"` → evaluated. Object form interchangeable: `label: { jsonata: "value.subject" }` ≡ prefix form. Escape a literal `$:` with `"$$:..."`. Non-strings never evaluate. Reserved structural keys are literal-only: `component`, `id`, `data.collection`, `data.state`, `data.latest`, `data.endpoint`, `data.poll`, `data.inline`, `on_<event>.action`, `slot`, `config.pagination.mode`.

The current row/node/scope is the implicit root — reference fields with **bare names** (`subject`, `value.body`). `$user`, `$factory`, `$page`, `$rowIndex` are available named scope vars depending on site.

> **Common mistake — no `$.field`.** The mini-parser doesn't support the real-JSONata `$` root sigil. `"$:$.prospect_name"` silently fails (resolver returns `undefined`, renderer falls back to the literal string). Always use bare names: `"$:prospect_name"`, `"$:'Review: ' & prospect_name"`, `"$:touch_count >= 3 ? 'exhausted' : 'active'"`.

### Capabilities & limits

`$:` is a hand-rolled subset, not full JSONata — projection, defaults, concat, comparisons, conditional visibility. It does NOT build or reshape data. Unsupported expressions fail silently (resolver returns `undefined`, renderer falls back to the literal string).

| ✅ Supported | ❌ Not supported (silently fails) |
|---|---|
| Literals (string/number/bool/`null`) | Object construction `{ ... }` |
| Field access — bare name + dotted path | Variable bindings `:=` |
| String concat `&` | Statement blocks `;` |
| Compare `= != < <= > >=` | Array transforms/map/reduce/lambdas |
| Arithmetic `+ - * /`, unary `-` | Predicates/filters `a[pred]`, regex |
| Logical `and`/`or`/`not` | `$` root sigil / `$$` root-array sigil |
| Ternary `cond ? then : else` | Higher-order functions |
| Builtins `$uppercase` `$lowercase` `$substring` `$string` `$number` `$boolean` `$not` `$length` | Any other builtin |

Two traps: (1) **Only string leaves evaluate** — `resolveValue` evaluates only a string starting `$:`; the object form `data: { $: "<expr>" }` passes through literally, so evaluate a whole expression with the string form `data: "$: <expr>"`. (2) **No object building** — you cannot construct/reshape a stored object in YAML (`data: "$: { md: ..., tiv: a + b }"` fails).

**Guidance — derive in an agent, not the UI.** When a stored object needs to be built/reshaped/computed, have the UI write raw form fields (a `data_field` slice, or plain/`$:`-projected leaves), and let a Python agent (`tf.on_state(collection, state).do(...)`) read the raw row and write the derived shape — `$:` projects and formats, it doesn't construct.

### `$:` evaluation is render-time only (Model A doctrine)

One evaluator, `ComponentRenderer`, at render time, against the current DataRef — parents never pre-evaluate a child's YAML. If a click-handler needs the clicked subject visible to children, the parent publishes the subject into the DataRef instead: `force_directed` node click merges `{ node: <clonedNode> }`, `scatter` point click merges `{ point: <clonedPoint> }`, `table` row click / `row_actions` merges `{ row: <clonedRow> }`. Children in the opened subtree read `$:node.type`, `$:point.label`, `$:row.invoice_id` directly. The merge is shallow over the existing DataRef root — siblings keep seeing form state. The **`open` action** likewise publishes its `subject:` param before activating the modal — under the `row` key when the subject carries `_tf_subjectKey: 'row'` (table-origin / the chat `open_ui_modal` tool), else under `subject` — so a Button-open or a chat-opened modal body reads `$: row.<field>` / `$: subject.<field>`.

**Which leaves can read a published subject (no `data:` block)** — only DataRef-aware leaves: `detail_list` ✅ (the canonical label→value record for a subject; no-`data:` mode reads each `fields[].field` from DataRef — use instead of stacked read-only `text_input`s), `text_input`/`textarea` ✅ (reads `field` from DataRef, `read_only: true` for display cells, `format:` still runs), `markdown` ✅ (no-`data:` mode reads `field` from DataRef, renders its own empty state when blank), `tag_list` ✅ (reads `field` from DataRef, a string-array field renders one chip per item, `empty_text` for zero). `metrics` ❌ — reads only via its own `data:` block (`useBoundData`), no DataRef fallback, renders `<EmptyState>` against a published subject; use `detail_list` instead.

A composite "Overview tab" (field record + summary) is built from `detail_list` + `markdown`, not `metrics`. Tab the body with `tabs` (`slot: tab`/`slot: panel` pairs) directly under the modal's `slot: body`.

### `open:` accepts a string id only

Click-opens-a-component contracts (`config.on_node_click.open`, `config.on_point_click.open`, table `detail_modal`, any future `open:`) take a **string id only** — the `id:` of an id-registered component (a `modal`) mounted anywhere in the same view.

**"Sibling" is about the id registry, not the DOM.** `open:` resolves against the page-level id registry (the nearest `DataRefProvider`), NOT literal tree siblings — and a `modal` is React-portaled, so its position in the layout is irrelevant as long as it's mounted. **Declare modals in the top-level `default_ui.modals` array** (a sibling of `default_ui.layout`, keyed by `id:`) — they mount page-level in the same registry, so any trigger opens one by id and one def is reusable from many sites (see `ui-modal`). You therefore never **wrap the layout in a `layout_column` to make a modal a "sibling"** — that wrapper is a redundant singleton (a modal isn't in-flow) that `check_ui`/`edit_ui` flag; keep a root `tabs` as the single root node. Nesting a modal inside the layout tree still works but is **deprecated** (`edit_ui` warns) — move it to `default_ui.modals`.

```yaml
- component: force_directed
  config:
    on_node_click:
      open: "$: node.type = 'state' ? 'state_modal' : 'agent_modal'"
- component: modal
  id: agent_modal
  ...                              # dormant until triggered
- component: modal
  id: state_modal
  ...
```

Inline object form (`open: { component: modal, ... }`) is rejected by the strict gate — always declare the modal as a sibling with `id:` and reference it. `open:` may be a literal string or a `$:` expression yielding a string at click time. Id-ref targets are dormant until triggered. If resolution yields `undefined`, a non-string, or an unmatched id, the renderer fires an error toast and renders nothing — no silent failures.

## Actions

Buttons, table `row_actions`, force-graph node clicks, and any interactive surface emit through top-level event-handler keys (`on_click`, `on_change`, `on_row_click`, `on_node_click`, `on_point_click`, `on_select`, `on_blur`). Each handler is an object whose `action:` names a dispatch target, with flat sibling params (no `args:` nest). A closed set of canonical actions is handled by the dispatcher; everything else bubbles to the host page via `custom:<name>`.

### Canonical action enum (6 entries)

| Action | Effect | Required | Optional |
|---|---|---|---|
| `save_data_item` | PUT `/api/factories/{factory}/data/{collection}/{key}` with `{ data?, state? }`; `data` **shallow-merges** into `value` (see Write semantics). Form saves, state transitions, and triggering agents (write to the agent's input collection/state, `key: $uuid` per click). | `collection`, `key` | `state`, `data`, `data_field` |
| `delete_data_item` | DELETE `/api/factories/{factory}/data/{collection}/{key}`; cascades to `factory_vectors`. | `collection`, `key` | — |
| `close` | Deactivate the host with `id:`, or top of the activation stack when omitted. Symmetric with `open`. | — | `id` |
| `open` | Activate a registered sibling id. Same as `on_<event>.open: <id>` short-form. | `id` | `subject` (onto DataRef root) |
| `set_url_param` | Client-side nav: write a URL query param, no backend contact. Sibling `key_from_url` reader re-renders. | `param`, `value` | `history` (`push` default \| `replace`) |
| `custom:<name>` | Bubbles through `onAction` to the host page, which interprets it (`custom:start_factory`, `custom:save_and_restart`). | varies | varies |

Only these six are accepted — any other `action:` value is rejected by the strict gate.

**Universal modifier:** any action accepts `then_close: true` to synthesise a follow-up `close`. Canonical actions close on success; `custom:*` closes optimistically (immediately after dispatch — no success signal comes back from custom handlers).

### Write semantics — `data` is a shallow merge, never a wipe

The backend PUT applies `data` as a shallow merge into `value` (`value || patch`): top-level keys in `data` overwrite, keys absent are preserved, nested objects replace wholesale (not deep-merged) — a value-wipe is structurally impossible. `{ data, state }` merges `data` and sets `state`; `{ data }` merges `data` (existing `state` preserved on update, new row defaults `state: new`); `{ state }` sets `state` only, `value` untouched. Full-overwrite (`replace: true`) and key-deletion (`unset: [...]`) were designed but deferred (YAGNI) — derive-and-rewrite in a Python agent if needed.

**Where the `data` patch comes from** (every dispatcher resolves its action spec — including any `data:` map — against its local context first): **`data: { ... }`** — explicit authored map, honoured on every dispatcher, becomes the PUT `data`; leaves may be `$:` projections, but you cannot synthesise a whole map inside one `$:` expression. **`data_field: <field>`** — pulls a dot-path slice out of the resolved data (Button snapshot or authored `data:` map) and PUTs just that slice; may be nested (`data_field: rubric.weights`). **Button/Modal-footer with neither** — auto-attach the live DataRef snapshot ⇒ full-snapshot save; because the write merges, keys absent from the snapshot still survive. **Bare dispatchers** (Table `row_actions`, `select.on_change`, input `on_blur`, `ConfirmDestructiveModal` confirm) do NOT auto-attach a snapshot — state-only path (`data` omitted, `value` preserved, only `state` flips), for pure state transitions.

E.g. `on_click: { action: save_data_item, collection: prompts, key: "$: prompt_key", data_field: text, then_close: true }` slices the snapshot down to just `text` instead of saving the whole row. `data_field` reads from the live DataRef, including in-modal edits since open. To save several fields from a non-Button dispatcher, point `data_field` at a shared parent object, or author an explicit `data:` map with a `$:` leaf per field. Canonical shape is a top-level event key with flat sibling params — see "One example per event type" below.

**Multi-action dispatch — array form on the same event key** (`notifications` below is a domain collection — never a reserved `_`-prefixed one):

```yaml
- component: button
  on_click:
    - { action: save_data_item, collection: rubric, key: default }
    - { action: save_data_item, collection: notifications, key: $uuid, state: rubric_updated }
```

### One example per event type

`on_click` (button): `{ action: save_data_item, collection: review_queue, key: "$: id", state: approved }`. `on_change` (select): `{ action: save_data_item, collection: tickets, key: "$: ticket_id", data_field: status }`. `on_blur`, save-on-defocus (textarea): `{ action: save_data_item, collection: invoices, key: "$: invoice_id", data_field: notes }`. `on_select`, combo/list (multi_select): `{ action: save_data_item, collection: prospects, key: "$: id", data_field: tags }`. `on_row_click`, opens registered modal by id (table): `{ open: invoice_detail_modal }`. `on_node_click` (force_directed): `{ open: "$: node.type = 'state' ? 'state_modal' : 'agent_modal'" }`. `on_point_click` (scatter): `{ open: point_detail_modal }`.

### `key: $uuid` — fresh-key-per-click pattern

When a button needs a brand-new row each click (e.g. fire-and-forget message), set `key: $uuid` — resolved to `crypto.randomUUID()` at action-dispatch time.

```yaml
# collection = agent's input collection (never `_`-prefixed); key: $uuid → crypto.randomUUID() at click time
- component: button
  on_click: { action: save_data_item, collection: Plan, key: $uuid, state: requested }
```

Canonical UI-triggered-agent pattern: each click writes a fresh row in the agent's input collection at the input state, and the agent subscribes with `@tf.on_state('Plan', 'requested').do` / `def handler(row): ...` (MUST transition or `.remove()` the row). There is ONE model: write a row into a domain collection at a state a consumer subscribes to. `_`-prefixed collections are reserved — never write to one. The agent's input collection/state is part of the contract; name them in factory.yml so UI and agent agree.

### `then_close: true` — close-after-action (universal)

Add `then_close: true` to any `on_click` action for a follow-up `close` via the activation stack (no explicit modal id needed). Timing: `save_data_item`/`delete_data_item` fire the close AFTER the HTTP request resolves successfully; `custom:<name>` fires it OPTIMISTICALLY (immediately after dispatch) — if the custom op fails, the modal still closes.

```yaml
- { component: button, on_click: { action: save_data_item, collection: rubric, key: default, then_close: true } }
- { component: button, on_click: { action: custom:save_and_restart, then_close: true } }   # optimistic close
```

`action: close` (symmetric with `open`) takes optional `id:` — omitted closes the top of the activation stack: `on_click: { action: close, id: edit_modal }` or bare `on_click: { action: close }`.

### `action: set_url_param` — URL-driven view navigation

SET half of the symmetric pair with `key_from_url` (see Data binding Mode 6). Use it when a click should pick "which thing is shown," per-tab, bookmarkable, back-button-navigable — without a DB write or a resolver agent.

```yaml
# Left pane: a page list. Clicking a row selects which page the viewer shows.
- component: table
  data: { collection: wiki }
  on_row_click: { action: set_url_param, param: wiki_select, value: "$: row._key", history: push }

# Right pane: a viewer bound to the SAME param. Re-renders to follow the click.
- component: markdown
  config:
    field: content_md
    wiki_link: { param: wiki_select }   # [[slug]] inline links set the same param
  data: { collection: wiki, key_from_url: wiki_select }
```

`param` (required) — query-param name, must match the reader's `key_from_url`. `value` (required) — resolved against the clicked subject, slug colons auto-encode via `URLSearchParams`. `history` — `push` (default, Back/Forward work) or `replace` (churny selectors; `tabs` uses this internally). Notifies any sibling `key_from_url` reader on the same param — re-renders in-app, no reload; tabs are independent. Pure client-side, no `/api` call, no chat-equivalent MCP tool.

### State transitions are a `save_data_item` (no worker needed)

"A reviewer moves Email from Drafted → Approved" is just `save_data_item` with `state:` set (see `on_click` above) — a state-only write is a state patch (`value` preserved), firing `{factory}.{collection}.{state}` NOTIFY so an agent that needs to react subscribes via `tf.on_state('review_queue', 'approved')`. Don't write a worker whose only job is translating a topic into a state write — let the UI write the state.

`factory.yml`'s `states:` entry `manual_transition_states:` is the authoritative list of transitions a reviewer may perform from the UI on rows in that state; UI authors wire matching `save_data_item` buttons, and the chat agent exposes the same list as MCP tools (GUI ↔ chat parity).

## Topic / event patterns

All backing data lives in `factory_data`. Writes fan out: Postgres NOTIFY `{factory}.{collection}.{state}` on state changes (INSERT/UPDATE changing state) — Python workers subscribe via `tf.on_state()`. Postgres NOTIFY `{factory}.{collection}._changed` on any INSERT/UPDATE — consumed server-side by `eventBus.js`, which fans out to SSE `tf.{factory}.{collection}.changed` (any write; what UI `data: { collection: ... }` bindings consume via `useBoundData`) and SSE `tf.{factory}.{collection}.{state}.{op}` (state+op-specific, for custom `useEvents` subscribers). Components with a `data:` block auto-subscribe to the SSE `.changed` topic and re-fetch on each event — no manual wiring. The legacy `_changed` NOTIFY channel is now server-internal; browsers no longer listen to it directly.

## Theme

Components use CSS custom properties for chrome (backgrounds, text, borders, status colours, focus rings); factory-passed palettes (e.g. `scatter.color_map`) may be raw hex. App-global tokens (set at orchestrator boot from `THEME_*` env vars): Brand `--primary-50`…`--primary-900`, `--secondary-50`…`--secondary-900`, `--tertiary-50`…`--tertiary-900`; Status `--success-500`/`--warning-500`/`--error-500`/`--info-500` (+ 50–900 variants); Surface `--bg-primary`/`--bg-secondary`/`--bg-tertiary`; Text `--text-primary`/`--text-secondary`/`--text-muted`; Border `--border-color`. Use inside a top-level `style:` block (or a dynamic JSONata-evaluated style value):

```yaml
- component: metrics
  style: { color: "$: $.score >= 80 ? 'var(--success-500)' : 'var(--warning-500)'" }
```

Named colour scales (categorical/sequential/diverging) for chart components are documented per leaf. Raw hex inside chart axis chrome is rejected; factory-owned data palettes (`color_map`) remain raw hex.

## Pre-loaded data sources (DataRef preload)

Declare named data sources at the top of `default_ui` to fetch factory_data before rendering. Each becomes a key on the page-level DataRef, so form leaves can bind to `field: <name>.nested.path` and edits save back via `save_data_item`.

```yaml
default_ui:
  data_sources:
    rubric: { collection: rubric, key: default }
    personas: { collection: personas, key: all }
  layout:
    component: tabs
    children:
      - { component: tab, slot: tab, title: Rubric }
      - component: layout_column
        slot: panel
        config: { gap: 12px }
        children:
          - component: code_editor
            config: { field: rubric, language: yaml, serialize: yaml }   # bound to preloaded factory_data.rubric.default
          - component: button
            config: { label: Save, variant: primary }
            on_click:
              - { action: save_data_item, collection: rubric, key: default }
              - action: save_data_item                    # trigger the pipeline; agent listens via
                collection: rubric                         #   tf.on_state('rubric', 'updated')
                key: $uuid
                state: updated
```

## Example: 2-tab dashboard

Slot-paired tabs (one `tab` marker + one panel container per tab, in declaration order):

```yaml
default_ui:
  layout:
    component: tabs
    children:
      # --- Tab 1: Overview ---
      - { component: tab, slot: tab, title: Overview, config: { icon: chart-pie } }
      - component: layout_row
        slot: panel
        config: { gap: 16px }
        children:
          - component: layout_column
            config: { flex: 2, gap: 16px }         # per-child flex is config, not style
            children:
              - component: metrics
                data: { collection: stats, state: summary, latest: true }
                config: { metrics: [ { field: total, label: Total, format: number } ] }
              - component: card
                title: Chart
                config: { flex: 1 }                # fills the remaining column height
                children:
                  - component: line_chart
                    data: { collection: stats, state: summary, latest: true }
                    config: { data_field: daily, x_field: day, y_field: count }
          - component: card
            title: Assistant
            config: { flex: 1 }
            style: { maxWidth: 360px }              # maxWidth is a real CSS constraint — stays in style
            children: [ { component: chat_panel } ]

      # --- Tab 2: Data ---
      - { component: tab, slot: tab, title: Data, config: { icon: table } }
      - component: table
        slot: panel
        data: { collection: invoices }
        config:
          key_field: id
          columns: [ { field: name, label: Name }, { field: status, label: Status } ]
```
