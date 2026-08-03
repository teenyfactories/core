# ui-code_editor

## Purpose
Multiline code editor (Monaco) with syntax highlighting, git diff markers, and serialization for Python, YAML, JSON, or markdown.

## When to use / when NOT
**Use:** Scripts, config blocks, structured text with syntax highlighting and git diff.
**NOT:** Single-line strings (use `text_input`); binary blobs.

## YAML shape
```yaml
- component: code_editor
  config:
    field: script                 # DataRef path
    label: Script
    language: python              # python | yaml | json | markdown
    serialize: yaml               # yaml | json | null (raw string)
    minimap: false
    read_only: false
    font_size: 13
    diff_source:                  # optional; omit for no diff
      kind: factory_action_head
      action_field: data.data.id  # resolves actionId for HEAD endpoint
```

> The doubled `data.data.` above is not a typo: the outer `data` is the clicked
> subject (uniform across every leaf — `ui-common` § `data`), the inner one is
> that node's own `data` payload. The leaf-specific spelling `node.data.id`
> still resolves and is **deprecated** (`check_ui` warns).

## Config keys
| Key | Type | Default | Meaning |
|---|---|---|---|
| `field` | string | required | DataRef path to the code string. |
| `label` | string | — | Display label above the editor. |
| `language` | enum | `python` | Syntax highlighting: `python`, `yaml`, `json`, `markdown`. |
| `serialize` | enum | `yaml` | Format for storage: `yaml`, `json`, or `null` (raw string). |
| `minimap` | bool | `false` | Show Monaco minimap column. |
| `read_only` | bool | `false` | Disable editing; display-only. |
| `font_size` | int | `13` | Editor font size (px). |
| `theme` | enum | — | Editor theme: `vs-dark` or `light`. |
| `line_numbers` | bool | `true` | Show line numbers in the gutter. |
| `word_wrap` | bool | `true` | Enable word wrapping for long lines. |
| `hint` | string | — | Optional help text shown via `?` info icon beside the label. |
| `diff_source` | object | absent | Enable inline diff decorations (see below). |

## Data & events
- **Live buffer:** typed into the editor; persisted to `field` on blur or programmatic submit.
- **Inline diff (when `diff_source` set):** gutter decorations (green/blue/red markers) update on keystroke (debounced 200ms), on HEAD arrival, or on `tf.{factory}.editor.code_changed` broadcast.
- **Diff decorations:**
  - **Green bar:** line in buffer, not in HEAD (new).
  - **Blue bar:** line content differs from HEAD (modified).
  - **Red wedge:** one or more HEAD lines deleted before this buffer line (removed).
- **Footer badge:** Shows diff anchor (`diff vs <sha7>`) or state (`New file (untracked)`, `No commits yet`, etc.).

## Example
```yaml
- component: code_editor
  config:
    field: data.data.code
    label: Python Script
    language: python
    read_only: false
    diff_source:
      kind: factory_action_head
      action_field: data.data.id
```

## Gotchas
- **Diff always-on when configured.** No toggle button; `diff_source` alone enables passive inline markers. Removes complexity vs. the previous toggle-based approach.
- **Non-git factories degrade silently.** If git HEAD endpoint fails or factory isn't git-backed, no decorations or badge appear; no error thrown. Safe for shared YAML across git and non-git installs.
- **Single-pane editor.** The editor does not use Monaco's `DiffEditor` (two-pane split). The buffer is source of truth; HEAD is read-only reference shown as decorations.
- **Hover per instance.** Decoration hover messages are scoped to the specific editor instance (registered via `hoverMessage`, not global `registerHoverProvider`).
- **Gutter CSS global.** Diff marker classes (`tf-code-diff-{added,modified,removed}`) live globally in `styles/globals.css` because Monaco renders the gutter in a portal.
