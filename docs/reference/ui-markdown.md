# ui-markdown

**Purpose:** Render markdown with dual-mode binding (explicit data or DataRef fallback) and wiki-link navigation.

**When to use / when NOT:**
- Use for rich markdown content (body, briefs, guidance, chat).
- Use wiki links for in-app cross-references (requires `wiki_link` config).
- Don't use for chat bubbles without `wiki_link` — links render inert anyway.

**YAML shape:**
```yaml
# Mode A — explicit data binding
- component: markdown
  data: { collection: docs, state: published, latest: true }
  config: { field: body, max_height: 400px }

# Mode B — DataRef fallback
- component: markdown
  config: { field: context_brief_md }
```

**Config keys:**
- `field` (required): dot-path to markdown field (e.g. `context_brief.tone_guidance`).
- `max_height` (optional): CSS height limit.
- `wiki_link` (optional): `{ param: <url-param-name> }` to enable `[[key]]` clicks.

**Data & events:**
- Explicit mode: `data:` block fetches collection/state/latest rows.
- DataRef mode: reads from parent DataRef (table detail modal, form section, preloaded data_sources).
- Links: `[text](url)` → external anchor (`target="_blank"`); `[[key]]` → styled span (inert) or in-app link (if `wiki_link` set). Clicks set `?<param>=<key>` via history push.

**Example:**
```yaml
- component: markdown
  data: { collection: wiki, key_from_url: wiki_select }
  config: { field: content_md, wiki_link: { param: wiki_select } }
```

**Gotchas:**
- Dot-paths work in both modes; verify field exists at path.
- Wiki links without `wiki_link` render as inert spans (safe for chat, no nav).
- Stale/deleted wiki keys show "not found"; missing param shows empty state.
