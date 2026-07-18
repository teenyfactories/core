# ui-force_directed

## Purpose

Force-graph layout component for visualizing state machines, agent diagrams, hierarchies, and entity-relationship networks. Binds to a single row containing a graph object (`nodes`, `links`); node and edge actions route through the canonical action enum (`save_data_item`, `delete_data_item`, `close`, `custom:<name>`).

## When to use / when NOT

**Use:** network diagrams, state graphs, entity relationships, hierarchies.

**NOT:** per-entity graphs in modals. Binds by collection, reads `rows[0]` only; no inline `$:` resolution. Write each entity's graph to a dedicated collection, then bind the modal's `force_directed` filtered to that entity.

## YAML shape

```yaml
component: force_directed
data:
  collection: ring_graph
  state: ready
  latest: true
config:
  show_arrows: false
  node_types:
    claimant: { shape: circle, radius: 18, fill: "#ef4444", icon: user }
    vendor:   { shape: rounded_rect, auto_width: true, fill: "#f59e0b", icon: wrench }
  on_node_click:
    open: "$: node.type = 'claimant' ? 'claimant_modal' : 'vendor_modal'"
  empty_message: "No data available"
  charge_strength: -2000
```

## Data & events

**Graph structure** (from row's `data`): `{nodes: [{id, type, label?, data: {fill?}}], links: [{source, target, label?}]}` (`edges` alias OK).

Per node: `id` (required), `type` (selects `node_types`), `label` (display; falls back to `data.name`/`id`), optional `data.fill` (hex override). Per link: `source`/`target` node ids, optional `label`.

Click handlers activate modals by string `id` only; modal descendants read `node.<field>`.

## Config keys

**Node rendering:** `node_types` (map type → shape/radius/fill/stroke/icon/label_position/auto_width); shapes: `circle`, `rounded_rect`, `rect`, `none`. `on_node_click: {open: string_modal_id}`. Fills are raw hex (factory-owned category palette).

**Physics:** `charge_strength` (default `-2000`), `charge_exponent` (default `1.5`), `charge_max_distance` (default `Infinity`; repulsion-only cutoff).

**Edge geometry:** `arrow_offset_start` (default `0`; source inset), `arrow_offset_end` (default `0`; target inset). `passthrough: true` ignores insets.

**Other:** `filter`, `empty_message`, `show_arrows`.

## Gotchas

- **Insets & passthrough:** `passthrough: true` on a node type ignores insets (connects at centre, no arrowhead). Insets are px, applied on top of node radius.
- **Charge falloff:** `charge_max_distance` affects only repulsion, not link/center/alignment forces.
- **Fills:** raw hex, factory-owned category palette (not theme tokens). Component chrome (grid, selection ring) uses theme.
- **Modal ids:** `open:` must be string id only; inline object form rejected.
