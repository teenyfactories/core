# UI component reference — index

The composable UI system builds factory dashboards and editor pages from a declarative YAML layout. Components nest recursively inside `layout_row`/`layout_column` primitives, bind to live data, and dispatch a small set of standard actions.

- **ui-common** — shared vocabulary: layout, data binding, actions, styling, and validation rules common to every component.
- **ui-table** — sortable, paginated table over a data collection, with per-column formatting and row actions.
- **ui-kanban** — drag-and-drop board that groups rows into columns by state.
- **ui-metrics** — grid of labeled key metric values.
- **ui-detail_list** — read-only label/value display of a single record.
- **ui-textarea** — multi-line text input.
- **ui-text_input** — single-line text, email, password, or number input.
- **ui-select** — single-selection dropdown.
- **ui-multi_select** — multi-selection checkbox combo box.
- **ui-markdown** — rendered markdown content, with optional in-app cross-links.
- **ui-force_directed** — force-directed graph for networks, hierarchies, and state diagrams.
- **ui-scatter** — two-variable scatter plot.
- **ui-bar_chart** — categorical bar chart, grouped or stacked.
- **ui-line_chart** — time-series and trend line chart.
- **ui-treemap** — proportional hierarchical treemap.
- **ui-layout-primitives** — the horizontal (`layout_row`) and vertical (`layout_column`) layout containers.
- **ui-card** — framed content panel with an optional header and footer.
- **ui-modal** — overlay dialog for focused, short-lived views and edits.
- **ui-tabs** — tabbed multi-panel view.
- **ui-code_editor** — syntax-highlighted code and configuration editor.
- **ui-tree_editor** — editable hierarchical tree view.
- **ui-file_explorer** — file browser for a factory's attached storage volume.
- **ui-commit_modal** — dialog for committing changes to version control.
- **ui-confirm_destructive_modal** — confirmation dialog for irreversible actions.
- **ui-button** — clickable action control.
- **ui-button_group** — layout wrapper for grouping related buttons.
- **ui-lightweight** — small display elements: status indicators, tags, placeholders, and loading/empty/error states.
- **ui-scope-locked** — chat and factory-editor panels available only within a factory page.
