# ui-scope-locked

These leaves declare `allowed_scopes: ['factory']` and only render inside a factory page. The renderer rejects them in editor / orchestrator scope.

Source: `ui-reference.md:2168–2187`

## chat_panel

Embedded inline chat connected to the LLM agent. When present in the composable UI, the floating chat FAB is hidden.

```yaml
component: chat_panel
```

## node_logs_panel

Live container-log stream. The factory editor uses it.

## node_controls_panel

Service start/stop/restart panel. The factory editor uses it.
