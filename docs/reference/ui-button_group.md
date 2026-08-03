# ui-button_group

## Purpose

Group related buttons in a flex container with configurable direction and alignment. Simplifies action bar layouts without nesting boilerplate.

## When to use / when NOT

**Use** when two or more action buttons need consistent spacing and alignment — form footers, modal action bars, row actions.  
**NOT** for single buttons (use `button` directly) or for nested button hierarchies (compose flat groups instead).

## YAML shape

```yaml
component: button_group
config:
  direction: horizontal    # horizontal | vertical
  justify: flex-end        # flex-start | center | flex-end | space-between
  gap: 12px                # pixel spacing between children
children:
  - component: button      # or other interactive leaf
    config: { ... }
    on_click: { ... }
  - component: button
    config: { ... }
    on_click: { ... }
```

## Config keys

| Key | Type | Default | Notes |
|---|---|---|---|
| `direction` | string | `horizontal` | Flex direction: `horizontal` or `vertical` |
| `justify` | string | `flex-start` | Flex `justify-content`: `flex-start`, `center`, `flex-end`, `space-between` |
| `gap` | string/number | `8px` | Spacing between children; CSS value (px, rem, etc) |

## Data & events

No data binding. Events bubble from children (`on_click`, `on_change`, etc.) — the group is a layout container only. Each child retains full event/action capability.

## Example

```yaml
component: button_group
config: { direction: horizontal, justify: flex-end, gap: 12px }
children:
  - component: button
    config: { label: Approve, variant: primary, icon: check }
    on_click:
      action: save_data_item
      collection: review_queue
      key: "$: prospect_id"
      state: approved
  - component: button
    config: { label: Reject, variant: danger, icon: times }
    on_click:
      action: save_data_item
      collection: review_queue
      key: "$: prospect_id"
      state: rejected
```

## Gotchas

- Button_group is **purely layout** — it does not filter, transform, or react to data. If you need conditional visibility, wrap the group in `show_when:` at the group's top level.
- Child events dispatch from the child's context, not the group's. No `on_click` on `button_group` itself.
- Vertical (`direction: vertical`) stacks buttons; use sparingly and prefer horizontal for most UIs.
