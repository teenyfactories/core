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
filter: "$: nodes[type='claimant']"
config:
  show_arrows: false
  node_types:
    claimant: { shape: circle, radius: 18, fill: "#ef4444", icon: user }
    vendor:   { shape: rounded_rect, auto_width: true, fill: "#f59e0b", icon: wrench }
  on_node_click:
    open: "$: data.type = 'claimant' ? 'claimant_modal' : 'vendor_modal'"
  empty_message: "No data available"
  charge_strength: -2000
```

## Data & events

**Graph structure** (from row's `data`): `{nodes: [{id, type, label?, data: {fill?}}], links: [{source, target, label?}]}` (`edges` alias OK).

Per node: `id` (required), `type` (selects `node_types`), `label` (display; falls back to `data.name`/`id`), optional `data.fill` (hex override). Per link: `source`/`target` node ids, optional `label`.

Click handlers activate modals by string `id` only; modal descendants read `data.<field>` — the uniform prefix for the clicked subject on every leaf (`ui-common` § `data`). The graph-specific spelling `node.<field>` still resolves and is **deprecated** (`check_ui` warns).

## Config keys

**Node rendering:** `node_types` (map type → shape/radius/fill/stroke/icon/label_position/auto_width); shapes: `circle`, `rounded_rect`, `rect`, `none`. `on_node_click: {open: string_modal_id}` — top-level on the component. This leaf keeps its own click keys (`on_node_click` / `on_edge_click` / `on_background_click`) rather than the universal `on_item_click`, because a graph has three distinct click targets and "the item" would be ambiguous. Fills are raw hex (factory-owned category palette).

**Physics:** `charge_strength` (default `-2000`), `charge_exponent` (default `1.5`), `charge_max_distance` (default `Infinity`; repulsion-only cutoff).

**Edge geometry:** `arrow_offset_start` (default `0`; source inset), `arrow_offset_end` (default `0`; target inset). `passthrough: true` ignores insets.

**Lanes:** group nodes into bands on X and/or Y. An axis turns ON when you set its
`lane_<axis>_field` — the `node.data.*` field to band by. `lane_x_field` bands
along X (vertical bands); `lane_y_field` bands along Y (horizontal swimlanes). Set
both → a matrix, one cell per x-value × y-value. There is **no band list** — the
component **discovers** each axis's bands from the distinct field values, and the
band **label IS the value**. Only tagged nodes are corralled; untagged nodes float
free. `lane_x_field` is the only required key to enable X lanes; the rest default:

| key | default | meaning |
|---|---|---|
| `lane_x_field` | `null` | node.data field for X bands; `null` = X lanes off |
| `lane_x_force_k` | `1500` | X wall-barrier strength (inverse-square coefficient); bigger = wider standoff gap |
| `lane_x_force_cap` | `12` | X wall-barrier force cap; bigger = harder to drag a lane across (reorder) |
| `lane_x_label_pos` | `top` | X label edge: `top` \| `bottom` |
| `lane_y_field` | `null` | node.data field for Y bands; `null` = Y lanes off |
| `lane_y_force_k` | `1500` | Y wall-barrier strength |
| `lane_y_force_cap` | `12` | Y wall-barrier force cap |
| `lane_y_label_pos` | `left` | Y label edge: `left` \| `right` |

Band **order is emergent** — by where each cluster settles along the axis; a lane
reorders only when its whole cluster's centre passes a neighbour's (order
hysteresis prevents flicker). Between the ordered lanes sit **n-1 walls** —
first-class sim bodies (position + velocity). A node feels a short-range
**inverse-square repulsion** from each of its two bounding walls; the
equal-and-opposite recoil moves the wall, so each wall **self-positions** into the
gap between the innermost nodes of its two lanes. The barrier climbs steeply as a
node nears a wall — a natural standoff **gap** opens (`force_k` sets its width) —
but is **capped** (`force_cap`), so a hard drag punches a node through. The walls
are free to **cross**: when two invert, the lanes they divide **swap** (no
snap-back). Lane interiors are force-free, so a wide cluster stays wide. The dashed
boundary lines ARE the wall bodies; labels pin to
the viewport edge, centred in each lane. Forces are 1-D per axis and independent
(so the matrix falls out of running both), applied from tick 0 (no ramp).
Convergence needs the tags roughly compatible with the links: tags that fight the
edge structure (a lane whose members' links pull them elsewhere) never settle.
Presentational only — lanes have no effect on factory runtime.

In the factory editor, X lanes are wired to the state/agent `stage:` field
(`lane_x_field: stage`).

**Other:** `empty_message`, `show_arrows`.

## Gotchas

- **Insets & passthrough:** `passthrough: true` on a node type ignores insets (connects at centre, no arrowhead). Insets are px, applied on top of node radius.
- **Charge falloff:** `charge_max_distance` affects only repulsion, not link/center/alignment forces.
- **Fills:** raw hex, factory-owned category palette (not theme tokens). Component chrome (grid, selection ring) uses theme.
- **Modal ids:** `open:` must be string id only; inline object form rejected.
- **Lanes vs `align_direction`:** don't combine on the laned axis — both fight for that axis's authority. Lanes should own the ordering on their axis; `align_direction` is for un-laned flow graphs.
