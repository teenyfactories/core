# ui-lightweight

Small display leaves for status and metadata rendering.

## placeholder

**Purpose:** Temporary content placeholder (e.g., "Coming soon...").

**Shape:** Simple text message.

**Config:**
```yaml
component: placeholder
config:
  message: "Coming soon..."
```

**Example:** `component: placeholder` with `message: "Coming soon..."`

## status_indicator

**Purpose:** Render status with dot, label, and optional last-action suffix (e.g., "Running (start requested)").

**Shape:** Coloured dot + auto-formatted label (snake_case → Title Case) + muted action suffix.

**Config:**
```yaml
component: status_indicator
config:
  field: status
  show_dot: true
  show_label: true
  show_pending_transition: false
  pending_transition_field: pendingTransition
  color_map:
    custom_running: '#10b981'
```

**Vocab (locked 2026-06-02):** pending/pulling/starting (info), running (success), stopping (warning), stopped/unknown (muted), crashed/oom_killed/crash_loop (error). Plus generic synonyms (active/online/failed/warning). Factory-owned `color_map` overrides individual statuses; use theme tokens (`var(--success-500)`) for brand chrome.

**Example:** `show_pending_transition: true` renders sticky `(start requested)` / `(stop requested)` / `(restart requested)` suffix.

## tag_list

**Purpose:** Render tags from a data field.

**Shape:** List of pills (outline or filled).

**Config:**
```yaml
component: tag_list
config:
  field: tags
  label: Tags
  variant: outline
```

**Example:** `variant: outline` (or `filled`) for visual control.

## container_status

**Purpose:** Summarize container lifecycle state across service statuses with inline control buttons.

**Shape:** Summary line (e.g., "N/N running (N problem)") + Start/Stop/Restart buttons.

**Config:**
```yaml
component: container_status
data:
  collection: services
  state: container_status
  latest: true
config:
  field: containers
  show_controls: true
  show_summary: true
  mode: default
```

**Vocab:** Auto-buckets into healthy (running), transient (pending/pulling/starting/stopping), problem (crashed/oom_killed/crash_loop), terminal (stopped/unknown). Emits `custom:start_factory` / `custom:stop_factory` / `custom:restart_factory`.

**Example:** `mode: compact` tightens row height; buttons stay visible (disabled when nothing running). `show_controls: false` hides Start/Stop/Restart buttons; `show_summary: false` hides the summary line.

## Status leaves

Theme-aware display states rendered directly from `useBoundData`:

- **spinner** – Suppressed for first 300 ms (no flicker on fast loads).
- **empty_state** – Honoured `config.empty_text`.
- **error_state** – Honours `config.error` and `error` from `useBoundData`.

**Example:**
```yaml
component: empty_state
config:
  empty_text: "No items yet — get started by adding one."
```
