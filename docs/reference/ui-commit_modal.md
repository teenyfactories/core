# ui-commit_modal

**Purpose**  
Specialised modal for the git "Commit changes" workflow. Presents a fixed body (commit-message textarea + read-only file list) and footer (Cancel / Commit buttons). On Commit, issues `POST /api/factories/{factory}/git/commit` with `{ message }` and emits `close` on success.

**When to use / when NOT**  
Use: Commit flow in a factory context. NOT slot-aware — body and footer are fixed. Do not use for custom commit-like flows requiring flexible layouts.

**YAML shape**

```yaml
- component: commit_modal
  id: commit_modal
  title: Commit changes
  config:
    width: 560px
    confirm_label: Commit
    cancel_label: Cancel
    message_placeholder: "Describe this change..."
    message_min_length: 1           # default 1
    factory: sqcdp                  # optional; defaults to enclosing FactoryContext
```

**Config keys**

| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `width` | string | — | CSS width; e.g. `560px` |
| `confirm_label` | string | — | Button label for commit action |
| `cancel_label` | string | — | Button label for cancel |
| `message_placeholder` | string | — | Textarea placeholder text |
| `message_min_length` | number | 1 | Min trimmed message length to enable Commit button |
| `factory` | string | enclosing FactoryContext | Factory scope for the commit endpoint |

**Data & events**

**Opening trigger** (sibling button):
```yaml
- component: button
  config: { label: Commit, variant: primary }
  on_click:
    action: open
    id: commit_modal
    subject:
      files:
        - { path: "factories/sqcdp/factory.yml", status: "M" }
        - { path: "factories/sqcdp/agents/new_agent.py", status: "A" }
```

**Subject shape:** `{ files: [{ path, status }, ...] }`. Status accepts git short codes (`M`, `A`, `D`, `R`, `C`, `U`, `??`); each renders as a tone-coloured tag using `theme.status.*`.

**Events:** `close` on success. Errors render inline in footer with generic message; detail goes to browser console only.

**Gotchas**

- Commit button disabled until trimmed message ≥ `message_min_length` AND at least one file present.
- Escape / backdrop / X disabled while POST in flight (prevents half-submitted state).
- Scope: `['factory', 'orchestrator']`. Editor scope rejected.
- Errors never show detail to user per generic-error rule.
