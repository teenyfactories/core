# UI guide — picking the right component

## How the library is organized

`ui-common` covers everything cross-cutting: universal top-level keys, the canonical config vocabulary (`*_field`, `format`, sort/pagination, colour), layout primitives, the six `data:` binding modes, JSONata (`$:`), slots, the closed six-action enum. Read it before your first build — every leaf doc assumes it. Each `ui-<name>` doc covers one leaf's `config:` shape, when to use it, its gotchas — fetch only the one you're about to build. Three docs bundle several small components under one id: `ui-lightweight` (status/placeholder leaves), `ui-layout-primitives` (the two layout primitives), `ui-scope-locked` (factory-page-only leaves).

## Decision quick-map

Sortable/paginated rows → `ui-table` · rows grouped by state, drag to transition → `ui-kanban` · KPI strip → `ui-metrics` · one record's fields, read-only → `ui-detail_list` · long-form editable text → `ui-textarea` · single-line input → `ui-text_input` · one-of-many choice → `ui-select` · many-of-many / tagging → `ui-multi_select` · rich text / briefs / wiki links → `ui-markdown` · state machine / entity graph → `ui-force_directed` · two numeric variables, clusters/correlation → `ui-scatter` · categorical comparison → `ui-bar_chart` · trend over time → `ui-line_chart` · flat proportional breakdown → `ui-treemap` · dashboard skeleton, side-by-side vs stacked → `ui-layout-primitives` · framed panel → `ui-card` · focused single-entity overlay → `ui-modal` · multi-view switcher → `ui-tabs` · script/config/diff → `ui-code_editor` · folder/org-chart editing → `ui-tree_editor` · browsing factory volume files → `ui-file_explorer` · git commit flow → `ui-commit_modal` · destructive-action confirm → `ui-confirm_destructive_modal` · single action → `ui-button`, grouped actions → `ui-button_group` · status dot/tag/loading/empty/error/container health → `ui-lightweight` · inline chat or factory-editor logs/controls → `ui-scope-locked`.

## Component catalog

**ui-table** — server-sorted/paginated collection view, per-column rendering, row actions, click-through detail. Row-and-column data at any volume. Not a single record (`ui-detail_list`) or free-form editing.

**ui-kanban** — state-grouped drag/drop columns, one per declared state; drag writes new state. Manual state moves by a person. No guard rails — any card can move to any column; not for read-only state views.

**ui-metrics** — grid of labeled KPI values from one bound `data:` source. Dashboard headers/summaries. Cannot read a clicked/published subject — only its own `data:`; use `ui-detail_list` for that.

**ui-detail_list** — read-only label→value record, DataRef-aware (no `data:` needed if a subject is published) or own `data:`. Replaces stacked read-only text inputs in a detail modal. Not editable, not multi-record.

**ui-textarea** — multi-line text, saves on blur (state-only patch). Notes/descriptions. Not single-line (`ui-text_input`) or code. In a footer `on_click` with no `data_field`, auto-attaches the FULL DataRef snapshot — scope with `data_field`.

**ui-text_input** — single-line text/email/password/number, short fields only. Field paths resolve against current scope — nested contexts may need a full dot-path.

**ui-select** — single-select dropdown, static `options:` only; `on_change` is a bare dispatcher, pair with `data_field`. Finite mutually-exclusive choice, no `options_from` — for dynamic/multi use `ui-multi_select`.

**ui-multi_select** — checkbox combo on an array field, static or dynamic (`options_from: states|agents`). Multi-item selection/tagging.

**ui-markdown** — markdown render, own `data:` or DataRef fallback, optional `[[key]]` wiki links (need `wiki_link` config; inert in chat bubbles otherwise). Rich content, briefs, guidance.

**ui-force_directed** — force-graph over one row's `{nodes, links}`; node click publishes `{node}` for a sibling modal. State machines, entity/agent networks. Reads `rows[0]` only — not per-entity graphs nested in a modal.

**ui-scatter** — 2D X/Y plot, quadrant crosshairs, categorical colour, click publishes `{point}`. Two numeric variables, clusters/outliers. Not one dimension (`ui-bar_chart`/`ui-line_chart`) or non-numeric axes.

**ui-bar_chart** — categorical bars, single/multi-series, grouped or stacked. Category comparison. Not dense time-series (`ui-line_chart`).

**ui-line_chart** — time-series/trend, `data_field` array mode. Values over time, multi-line comparison. Not a single value or hierarchical data.

**ui-treemap** — nested rectangles sized/coloured by value, flat array only. Proportional breakdowns. No true multi-level nesting yet; not time-series or category bars.

**ui-layout-primitives** — `layout_row`/`layout_column`, the only sanctioned layout primitives. Every new dashboard skeleton. The `grid` component they replaced still renders for old factories but must never be authored into new YAML.

**ui-card** — framed panel, optional header/body/footer; body auto-infers layout axis from children. Groups content inside a dashboard. Not a page shell (use layout primitives directly).

**ui-modal** — slot-aware overlay (header/body/footer); nested stateful children (tabs, filters) reset per open. Focused, short-lived single-entity view/edit. Not for anything route-worthy, or state that must persist across opens.

**ui-tabs** — slot-paired `tab`/`panel` markers (counts must match), URL-persisted at page level, transient in modals. Switching between full views in place. Not a toolbar/dropdown substitute or page-routing stand-in.

**ui-code_editor** — Monaco editor, syntax highlighting, optional git-diff gutter. Scripts/config/structured text. Not single-line strings; single-pane only, no split diff.

**ui-tree_editor** — client-side hierarchy editor over a flat `parent_id`-keyed collection; add/rename/delete. Folder structures, org charts. `max_depth` is UI-only (enforce server-side too); delete cascades to descendants with no confirm step.

**ui-file_explorer** — filesystem browser over a declared factory volume (list/upload/download/mkdir/delete); talks to the volume API, not `factory_data`. Requires the volume pre-declared in `factory.yml`.

**ui-commit_modal** — fixed-layout git commit flow (message + file list + Cancel/Commit); not slot-aware. Only for the commit workflow — use `ui-modal`/`ui-confirm_destructive_modal` for anything else.

**ui-confirm_destructive_modal** — shared confirm for destructive/conflict flows; dispatches a configured action, no HTTP of its own. Before any irreversible action. Cancel is the safe default on Escape/backdrop/X.

**ui-button** — dispatches one or more actions on click. Any user-triggered mutation/transition/navigation. Not a hyperlink (markdown link or table `link_field`) or a persistent toggle (`ui-select`).

**ui-button_group** — flex wrapper for 2+ buttons, no data binding or events of its own. Skip for a single button.

**ui-lightweight** — `placeholder`, `status_indicator` (locked status→colour vocabulary), `tag_list`, `container_status` (with `custom:start_factory`/`stop_factory`/`restart_factory`), `spinner`/`empty_state`/`error_state`. Each for its narrow display purpose.

**ui-scope-locked** — `chat_panel`, `node_logs_panel`, `node_controls_panel`: factory-page scope only, rejected in editor/orchestrator scope.
