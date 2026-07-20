# ui-file_explorer

## Purpose

OneDrive/Drive-like file browser over a factory **volume**. Provides filesystem browsing (not data subscription) with view modes, sorting, upload/download, and folder navigation.

## When to use / when NOT

**Use:** Browse local factory files (docker volume, S3 prefix on k8s).  
**NOT:** for `factory_data` state topics — `file_explorer` has no `data:` block and does not subscribe to factory collections.

## YAML shape

```yaml
component: file_explorer
config:
  volume: agreements          # required; must match factory.yml volumes: entry
  factory: optional_override  # defaults to FactoryContext
  default_view: list
  sort_field: name
  sort_dir: asc
  allow_upload: true
  allow_delete: true
  allow_mkdir: true
  empty_text: "This folder is empty."
```

## Config keys

| Key | Type | Default | Purpose |
|---|---|---|---|
| `volume` | string | — *required* | The `factory.yml` `volumes: - name:` value to browse. |
| `factory` | string | FactoryContext | Override factory whose volume is browsed. |
| `default_view` | `grid` \| `list` \| `details` | `list` | Initial view mode; user toggles live. |
| `sort_field` | `name` \| `type` \| `size` \| `mtime` | `name` | Initial sort column (client-side). |
| `sort_dir` | `asc` \| `desc` | `asc` | Sort direction. |
| `allow_upload` | bool | `true` | Drag-drop + Upload button. |
| `allow_delete` | bool | `true` | Per-entry delete with confirm. |
| `allow_mkdir` | bool | `true` | New Folder button. |
| `empty_text` | string | "This folder is empty." | Empty-state message. |

## Data & events

**No data subscription.** Fetches the orchestrator volume API directly:

```
GET    /api/factories/{factory}/volumes/{volume}/list?path=<subdir>
GET    /api/factories/{factory}/volumes/{volume}/download?path=<file>
POST   /api/factories/{factory}/volumes/{volume}/upload?path=<subdir>   (multipart, field `file`, 100 MiB)
POST   /api/factories/{factory}/volumes/{volume}/mkdir   { path }
DELETE /api/factories/{factory}/volumes/{volume}/entry?path=<file|dir>
```

All endpoints gated on `edit_factory` permission. Backend owns all path/key confinement.

## Example

```yaml
component: card
title: Agreement PDFs
children:
  - component: file_explorer
    config:
      volume: agreements
      default_view: details
      sort_field: mtime
      sort_dir: desc
```

## Gotchas

- **Volume declaration required:** volume must exist in `factory.yml` `volumes:` block. Undeclared volumes yield generic backend error.
- **Self-contained:** emits no actions; requires no sibling wiring.
- **Path confinement:** backend enforces all traversal safety and volume verification.
- **K8s:** for S3 volumes, orchestrator must hold bucket credentials.
- **Capabilities:** view toggle (grid/list/details), client-side sort (name/type/size/mtime), breadcrumb navigation, double-click folders, drag-drop/button upload, per-entry download/delete, New Folder. Folders always sort above files; long names truncate with ellipsis. Fills parent, survives window resize. Loading (300 ms–deferred), empty, and error states handled via shared leaves.

