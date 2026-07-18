# ui-button

## Purpose

A clickable button that triggers actions (state transitions, data mutations, page navigation). Buttons are the primary interactive affordance in dashboards — they dispatch `on_click` handlers to modify data, close modals, or signal custom events.

## When to use / when NOT

**Use** for user-driven state transitions, data mutations, or navigation. **Not** a link — use `markdown` with `[text](url)` or a `table` column with `link_field` for navigation. Not a toggle — use `select` (scalar) or `multi_select` (array) for stateful form inputs that persist on change.

## YAML shape

```yaml
component: button
config:
  label: Generate Invoice
  variant: primary           # primary | secondary | danger | ghost
  size: medium               # small | medium | large
  icon: plus                 # optional Font Awesome solid icon name
on_click:
  action: save_data_item
  collection: jobs
  key: "$: job_id"
  state: requested
```

## Config keys

| Key | Type | Default | Notes |
|---|---|---|---|
| `label` | string | required | Button text; rendered centered. |
| `variant` | enum | `secondary` | Visual style: `primary` (blue, CTA), `secondary` (neutral), `danger` (red/destructive), `danger-outline` (red outline), `ghost` (text-only, minimal chrome). |
| `size` | enum | `medium` | Button dimensions: `small`, `medium`, `large`. |
| `icon` | string | — | Optional Font Awesome v6 solid icon name (e.g. `check`, `trash`, `plus`). |
| `icon_position` | enum | `left` | Icon placement: `left` or `right` of label. |
| `disabled` | bool | `false` | When `true`, button is greyed out and non-clickable. |
| `loading` | bool | `false` | When `true`, button shows a loading spinner and is non-clickable. |

## Data & events

**`on_click` — button pressed.** Fires a single action or array of actions (multi-dispatch). Action semantics (canonical actions, params, write semantics) are detailed in [Actions](../ui-reference.md#actions). Most common: `save_data_item` (state transition + optional data patch) and `custom:*` (page-specific handler).

Buttons automatically resolve `$:` JSONata expressions in action params against the live DataRef snapshot (if the button is nested under a `data:` binding) or the current page context.

## Example

```yaml
- component: button_group
  children:
    - component: button
      config: { label: Approve, variant: primary, icon: check }
      on_click:
        action: save_data_item
        collection: review_queue
        key: "$: prospect_id"
        state: approved
        data: { approved_at: "$: now()" }
    - component: button
      config: { label: Reject, variant: danger, icon: times }
      on_click:
        action: save_data_item
        collection: review_queue
        key: "$: prospect_id"
        state: rejected
```

## Gotchas

- **Spacing.** Group with `button_group` or add parent `style: { margin, padding }` for standalone spacing.
- **Icon-only.** Omit `label` for icon-only render (square pad); rare — prefer labels for accessibility.
- **`then_close: true`** closes the modal/transient host after the action fires.
- **Action errors.** Backend failures (4xx/5xx) don't auto-retry or toast. Use `custom:*` + page handler.
