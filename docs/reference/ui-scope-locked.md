# ui-scope-locked

These leaves declare `allowed_scopes: ['factory']` and only render inside a factory page. The renderer rejects them in editor / orchestrator scope.

Source: `ui-reference.md:2168–2187`

## chat_panel

Embedded inline chat connected to the LLM agent. When present in the composable UI, the floating chat FAB is hidden.

```yaml
component: chat_panel
```

## node_logs_panel

Live container-log stream. The factory editor uses it. Config: `service_field` (DataRef path to an agent slug, scopes to one service), `factory_name` (override), `show_controls` (start/stop/restart buttons), `initial_levels` (string[] seeding the level filter on mount — subset of `debug|info|persona|warn|error|breakpoint`; absent/empty → all levels, e.g. `['warn','error']` opens issues-only).

## node_controls_panel

Service start/stop/restart panel. The factory editor uses it.
