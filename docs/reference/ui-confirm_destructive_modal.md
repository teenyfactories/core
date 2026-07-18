# ui-confirm_destructive_modal

## Purpose

Shared, presentational modal for confirming destructive actions. Issues no HTTP requests; dispatches a configured `confirm_action` through `onAction` so the host page can run the appropriate endpoint.

## When to use / when NOT

**Use for:**
- Git 409-conflict codepaths: `DIRTY_WORKING_TREE`, `LOCAL_AHEAD`, `REMOTE_AHEAD`, `FORCE_REQUIRED`
- Any destructive user flow requiring explicit confirmation with consequences listed

**NOT for:**
- Non-destructive confirmations (use a simpler modal)
- Editor scope (rejected by scope validation)

## YAML shape

```yaml
- component: confirm_destructive_modal
  id: <unique-id>
  title: "Confirmation title"  # optional if supplying via subject
  config:
    description: "Why this is destructive"
    consequences: ["impact 1", "impact 2"]
    confirm_label: "Do it"
    cancel_label: "Cancel"
    confirm_action:
      action: custom:<action-name>
    keep_open_on_confirm: false  # set true if host drives close
```

## Config keys

| Key | Type | Required | Notes |
|-----|------|----------|-------|
| `description` | string | Yes (or via subject) | Explanation of the destructive action |
| `consequences` | array of strings | No | Bulleted list of impacts |
| `confirm_label` | string | Yes (or via subject) | Button text for destructive action |
| `cancel_label` | string | No; default "Cancel" | Safe choice button text |
| `confirm_action` | object | Yes (or via subject) | Action dispatched on confirm (e.g., `{action: custom:git_force_push}`) |
| `keep_open_on_confirm` | boolean | No; default false | If true, host must close modal manually |
| `width` | CSS length | `480px` | Modal width |
| `max_width` | CSS length | `90vw` | Responsive max-width constraint |
| `max_height` | CSS length | `80vh` | Responsive max-height constraint |

## Data & events

**Subject fields** (supplied per-open via `on_click` → `subject`):
Override matching config fields. Recognized keys: `title`, `description`, `consequences`, `confirm_label`, `cancel_label`, `confirm_action`.

**onAction dispatch:**
Confirm button calls `onAction(confirm_action, params)` then auto-closes (unless `keep_open_on_confirm: true`).

## Example

```yaml
- component: button
  config: { label: "Push", icon: cloud-upload }
  on_click:
    action: open
    id: confirm_destructive_modal
    subject:
      title: "Force push required?"
      description: "Remote rejected non-fast-forward push."
      consequences:
        - "You will overwrite remote commits"
      confirm_label: "Force push"
      confirm_action:
        action: custom:git_force_push
```

## Gotchas

- **Cancel is safe default:** Cancel button auto-focused; Escape, backdrop, and X also close without dispatching
- **Styling:** Confirm button uses `theme.status.error` with danger accent stripe (top edge); no inline hex
- **Scope:** Allowed in `['factory', 'orchestrator']`; rejected in editor
- **Keep-open flow:** Use `keep_open_on_confirm: true` if waiting on subsequent async completion before dismissal
