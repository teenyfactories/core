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
# Event handlers are top-level on the component, NEVER under `config:`.
on_node_click:
  open: "$: node.type = 'claimant' ? 'claimant_modal' : 'vendor_modal'"
config:
  # Node visuals — one flat block; every value is a literal OR a `$:` expression.
  node_style:
    shape:  "$: node.data.type = 'claimant' ? 'circle' : 'rounded_rect'"
    radius: 18
    fill:   "$: node.data.type = 'claimant' ? '#ef4444' : '#f59e0b'"
    icon:   "$: node.data.type = 'claimant' ? 'user' : 'wrench'"
    auto_width: "$: node.data.type = 'vendor'"
  # Edge visuals — one flat block; every value is a literal OR a `$:` expression.
  edge_style:
    color: "#6b7280"
    width: 2
    arrow: true
  empty_message: "No data available"
  charge_strength: -2000
```

## Data & events

**Graph structure** (from row's `data`): `{nodes: [{id, type, label?, data: {…}}], links: [{source, target, label?, data?}]}` (`edges` alias OK).

Per node: `id` (required), node identity is `data.type` (values like `state`, `agent`, `worker`, `bundle`); a root-level `type` is lifted into `data.type` as a fallback for the older graph convention, so `$:` style expressions read `node.data.type` uniformly. `label` displays (falls back to `data.name`/`id`); optional `data.fill` is a per-node hex override that still wins over the resolved `node_style.fill`. Per link: `source`/`target` node ids, optional `label`, optional `data`.

Click handlers activate modals by string `id` only; modal descendants read `data.<field>` — the uniform prefix for the clicked subject on every leaf (`ui-common` § `data`). The graph-specific spelling `node.<field>` still resolves and is **deprecated** (`check_ui` warns). **Click-handler `$:` expressions see the RAW node** (root-level `node.type`), NOT the style projection below — the projection (and its `data.type` lift) applies only to `node_style` / `edge_style` / `bundle_collapsible`.

## Config keys

### Node styling (`node_style`)

One flat block, resolved **once per graph change** against the node's projection (see **Styling expressions** below). Every property is a literal OR a `$:` expression. A caller need only override the props it cares about; unspecified props keep the built-in default.

| key | default | meaning |
|---|---|---|
| `shape` | `circle` | `circle` \| `rounded_rect` \| `rect` \| `none` (`none` = icon/label only, still a full-radius hit target) |
| `radius` | `15` | circle / `none` radius (also icon sizing + hit radius) |
| `fill` | `#6b7280` | body fill (a per-node `data.fill` overrides this) |
| `stroke` | `#4b5563` | body outline colour (`none` = no outline) |
| `stroke_width` | `2` | body outline width |
| `text_color` | `#ffffff` | label / icon colour |
| `icon` | `null` | icon name (`clock`, `robot`, `database`, `bolt`, `user`, `server`, … — see the icon registry); not drawn when `label_position: center` |
| `icon_color` | `null` | icon colour override (falls back to `text_color`, or `fill` for `none`) |
| `label_position` | `bottom` | `bottom` (under the shape) \| `center` (inside it) |
| `auto_width` | `false` | `rounded_rect` only — pill sizes itself to its label text |
| `auto_width_padding` | `16` | horizontal padding added to the measured text width |
| `width` | `84` | fixed `rect`/`rounded_rect` width (ignored when `auto_width`) |
| `height` | `56` (`22` under `auto_width`) | `rect`/`rounded_rect` height |
| `corner_radius` | `8` | `rounded_rect` corner radius (auto-width pills default to `height/2`) |
| `font_size` | `12px` | label font size — applies to `label_position: center` and auto-width pills only; a `bottom` label is fixed at 12px |
| `label_field` | `null` | name a data field to use as the label |
| `label_formula` | `null` | `$:` expression for the label (**wins if both set**, even when it resolves to `undefined`) |

Label fallback when neither `label_field` nor `label_formula` is set: `node.label` → `node.data.name` → `node.id`.

**Worked example** — one block branching on `node.data.type` (state pill vs. icon-only agent/worker):

```yaml
node_style:
  shape:          "$: node.data.type = 'state' ? 'rounded_rect' : 'none'"
  radius:         15
  height:         22
  corner_radius:  11
  font_size:      "10px"
  fill:           "$: node.data.type = 'state' ? '#4b5563' : '#6b7280'"
  stroke:         none
  text_color:     "#ffffff"
  label_position: "$: node.data.type = 'state' ? 'center' : 'bottom'"
  auto_width:     "$: node.data.type = 'state'"
  icon:           "$: node.data.type = 'state' ? null : 'robot'"
  icon_color:     "$: node.data.type = 'worker' ? '#14b8a6' : '#f97316'"
```

### Edge styling (`edge_style`)

One flat block, resolved once per graph change against the edge's projection. Every property is a literal OR a `$:` expression.

| key | default | meaning |
|---|---|---|
| `color` | `#6b7280` | line colour |
| `width` | `2` | line width |
| `start_anchor` | `perimeter` | where the line docks at the SOURCE node — `perimeter` \| `center` |
| `end_anchor` | `perimeter` | where the line docks at the TARGET node — `perimeter` \| `center` |
| `arrow` | `true` | draw a target-end arrowhead |
| `offset_start` | `0` | px inset at the source perimeter (moot when `start_anchor: center`) |
| `offset_end` | `0` | px inset at the target perimeter (moot when `end_anchor: center`) |

**`center` vs `perimeter`:** `center` docks the line at the node's centre point (arrowhead-less waypoints like state pills read best this way); `perimeter` docks at the node boundary. When `bundle_links` is on, a `perimeter` end auto-uses the node's flow **port** (its in/out hemisphere) instead of the plain radius point.

**Passthrough replacement** — the old `passthrough` node flag is now expressed per-edge. Edges into/out of a `state` dock at its centre with no arrowhead; every other edge uses the flow port with a small gap before the target:

```yaml
edge_style:
  start_anchor: "$: edge.source.data.type = 'state' ? 'center' : 'perimeter'"
  end_anchor:   "$: edge.target.data.type = 'state' ? 'center' : 'perimeter'"
  arrow:        "$: edge.target.data.type != 'state'"
  offset_end:   "$: edge.target.data.type = 'state' ? 0 : 10"
```

The arrowhead marker itself is drawn in a single global colour, so it does not follow a per-edge `edge_style.color`; the line body does.

### Styling expressions

`node_style`, `edge_style`, and `bundle_collapsible` are the only three knobs resolved against the graph **projection** — a position-free, cyclic, navigable view of the graph built once per graph change (never per animation tick). Vocabulary: **node · edge · source · target**, with `source_edges` / `target_edges` for a node's edges (there are no `in`/`out` terms).

**Node context** (`node_style`, `bundle_collapsible`):

| binding | is |
|---|---|
| `node.id` | node id |
| `node.data.*` | the node's data fields (`node.data.type` = identity) |
| `node.source_edges` | edges where this node is the **source** — i.e. **outgoing** edges |
| `node.target_edges` | edges where this node is the **target** — i.e. **incoming** edges |
| `data` | alias for `node.data` |

**Edge context** (`edge_style`):

| binding | is |
|---|---|
| `edge.id` | edge id |
| `edge.data.*` | the edge's data fields |
| `edge.source` / `edge.target` | the endpoint **nodes** (`edge.target.data.type`) |
| `source` / `target` | aliases for `edge.source` / `edge.target` |
| `data` | alias for `edge.data` |

Because `source`/`target` point at node projections, the graph is navigable (`edge.target.source_edges[0].target…`). The shared expression engine supports `$count`/`$length`, `.field` access, `[N]` literal indexing, and `[predicate]` filtering, so neighbour navigation works: `node.source_edges[target.data.type = 'state']` filters, `[0].target.id` indexes then reads. Full capability list and its scoped gaps: `ui-common` § "JSONata for dynamic values".

**Position (`x`/`y`) is deliberately NOT in the style context** — style is resolved once and cached, never re-run per tick, so an expression that reads position would go stale. Keep style expressions position-free.

### Event handlers

All `on_<event>` handlers sit **top-level on the component, never under `config:`** — `on_node_click`, `on_edge_click`, `on_background_click`, `on_node_drag_end`, `on_positions_update`. This leaf keeps its own three click keys rather than the universal `on_item_click`, because a graph has three distinct click targets and "the item" would be ambiguous. `on_node_click: {open: string_modal_id}` activates a modal by string `id`; the inline object form is rejected. `on_node_drag_end` and `on_positions_update` fire the drag / settle callbacks.

### Physics

`charge_strength` (default `-2000`), `charge_exponent` (default `1.5`), `charge_max_distance` (default `Infinity`; repulsion-only cutoff), `edge_distance`, `center_strength`, `edge_strength`, `damping`, `max_velocity`, `min_velocity`.

### Bundling (`bundle_links`)

The master switch for edge ports, the directional force, and the declutter fold. `bundle_links: true` docks edges at **flow ports** — each node gets a mean flow axis; its OUT-edges leave from the downstream hemisphere and its IN-edges enter the opposite (upstream) hemisphere, so an agent reads "in one side, out the other". Each edge biases its own port toward its neighbour (fanned across the hemisphere, not piled on one point), then curves via a Bézier. **Everything `bundle_*` is inert unless `bundle_links` is true.**

| key | default | meaning |
|---|---|---|
| `bundle_links` | `false` | master switch — ports + directional force + declutter fold |
| `bundle_bezier_offset` | `0.3` | control-point distance as a fraction of edge length |
| `bundle_bezier_min_offset` | `20` | floor (px) for that distance, so short edges still curve |
| `bundle_bezier_fan_bias` | `1` | port fan: `0` = aim straight at neighbour · `1` = pin to the flow-axis port |
| `bundle_directional_force` | `true` | tangential torque that swings each node's neighbours toward its flow port, so flow reads "in one side, out the other" — gated by `bundle_links` |
| `bundle_directional_force_strength` | `3.0` | peak tangential kick (px/tick); the profile self-caps at this, so it's also the ceiling |
| `bundle_directional_force_falloff` | `300` | softening / reach distance d₀ (px). Softened inverse-square in distance (exponent hardcoded 2); larger = the torque reaches farther |
| `bundle_directional_force_default_angle` | `0` | flow angle for a node with no clear axis (pure source/sink) |

The angular profile maxes out for a neighbour ≥90° off the port and decays to zero (as θ²) when it's placed right, so a settled layout doesn't jitter. Under lanes it **cooperates** rather than fights: on any edge crossing a lane wall, the laned-axis component of the torque is zeroed — lanes own hemisphere placement on that axis, the force only supplies the orthogonal straightening. (This is why it now defaults ON with lanes; the old version had to be disabled.)

### Lanes

Group nodes into bands on X and/or Y. An axis turns ON when you set its `lane_<axis>_field` — the `node.data.*` field to band by. `lane_x_field` bands along X (vertical bands); `lane_y_field` bands along Y (horizontal swimlanes). Set both → a matrix, one cell per x-value × y-value. There is **no band list** — the component **discovers** each axis's bands from the distinct field values, and the band **label IS the value**. Only tagged nodes are corralled; untagged nodes float free.

| key | default | meaning |
|---|---|---|
| `lane_x_field` | `null` | node.data field for X bands; `null` = X lanes off |
| `lane_x_force_strength` | `1500` | X wall-barrier strength (inverse-square coefficient); bigger = wider standoff gap |
| `lane_x_force_cap` | `12` | X wall-barrier force cap; bigger = harder to drag a lane across (reorder) |
| `lane_x_label_pos` | `top` | X label edge: `top` \| `bottom` |
| `lane_y_field` | `null` | node.data field for Y bands; `null` = Y lanes off |
| `lane_y_force_strength` | `1500` | Y wall-barrier strength |
| `lane_y_force_cap` | `12` | Y wall-barrier force cap |
| `lane_y_label_pos` | `left` | Y label edge: `left` \| `right` |

Band **order is emergent** — by where each cluster settles along the axis; a lane reorders only when its whole cluster's centre passes a neighbour's (order hysteresis prevents flicker). Between the ordered lanes sit **n-1 walls** — first-class sim bodies (position + velocity). A node feels a short-range **inverse-square repulsion** from each of its two bounding walls; the equal-and-opposite recoil moves the wall, so each wall **self-positions** into the gap between the innermost nodes of its two lanes. The barrier climbs steeply as a node nears a wall — a natural standoff **gap** opens (`force_k` sets its width) — but is **capped** (`force_cap`), so a hard drag punches a node through. The walls are free to **cross**: when two invert, the lanes they divide **swap** (no snap-back). Lane interiors are force-free, so a wide cluster stays wide. The dashed boundary lines ARE the wall bodies; labels pin to the viewport edge, centred in each lane. Forces are 1-D per axis and independent (so the matrix falls out of running both), applied from tick 0 (no ramp). Convergence needs the tags roughly compatible with the links: tags that fight the edge structure never settle. Presentational only — lanes have no effect on factory runtime.

In the factory editor, X lanes are wired to the state/agent `stage:` field (`lane_x_field: stage`).

### Declutter (collapse)

Enabled per NODE via `bundle_collapsible` — a `$:` predicate (or a literal bool) evaluated against each node's context (gated by `bundle_links`). e.g. `bundle_collapsible: "$: node.data.type = 'agent'"` gives every agent's in/out ends the collapse affordance; `false` (default) means nothing collapses. It folds spaghetti before it reaches the sim. The affordance lives on a **node's input/output end**, not on states. From the agent↔state flow (edge agent→state = the agent PRODUCES the state; edge state→agent = CONSUMES it) each state around a node is one of: a **bridge** — 1 producer + 1 consumer; ALL bridge states between the same producer→consumer PAIR fold into ONE (+) on the `from→to` path (one (+) per partner node, id `br:<from>><to>`); a **fan-out** — a node's pure sinks, grouped into ONE (+) on its output side (id `fo:<nodeId>`); a **fan-in** — a node's pure sources, ONE (+) on its input side (id `fi:<nodeId>`). Multi-producer / multi-consumer states are left alone.

A collapsible node with **>1 outputs** shows a faint translucent **(−)** cue at its output port; one with **>1 inputs** shows one at its input port (both brighten on hover, at the `bundle_links` flow-axis ports). Clicking an end folds that whole side — one (+) per bridge plus the fan (+) — into small **(+) circle nodes** on the lines; click a (+) to expand it.

Which STATE nodes are folded is carried on the state itself: `data.bundle_collapsed: true` marks a state folded. A (+) bundle renders only when **ALL** its member states are marked, so hand-marking half a fan does nothing. The component seeds its live set from these flags **once** at load, then owns it (the editor persists the set of folded state ids and re-injects the flags — a re-injected flag can't revert a user's expand). Nothing folds by default.

**(+) bundle nodes carry no `stage`, so the lane block skips them — they float free of the stage lanes.** A (+) is seeded at the **centroid** of the states it folds; expanding one **scatters** its states back around that spot. Requires `bundle_links` (the cues sit on the flow-axis ports).

| key | default | meaning |
|---|---|---|
| `bundle_collapsible` | `false` | `$:` predicate (or bool) per node — which nodes' in/out ends get the (−) cue (gated by `bundle_links`) |
| `data.bundle_collapsed` (per state node) | — | marks a state folded; a (+) shows when all its members are marked |

### Other

`empty_message`, `show_labels`, `show_grid`, `grid_size`, `grid_color`, `grid_opacity`, `allow_selection`, `selection_color`, `selection_stroke_width`.

## Gotchas

- **Styles compute once, no position:** `node_style` / `edge_style` / `bundle_collapsible` are resolved once per graph change and cached, never per animation tick. An expression that reads a node's `x`/`y` would go stale — position is deliberately absent from the style context. Keep style expressions position-free; branch on `data.*` and edge topology only.
- **`source_edges` = outgoing:** `node.source_edges` are the edges where the node is the edge's **source** — its **outgoing** edges — and `node.target_edges` are its **incoming** edges. The intuitive-but-wrong reading ("edges coming from my sources") is the opposite of what these mean. So a producer edge agent→state lands in `agent.source_edges` and `state.target_edges`.
- **Click handlers see the raw node:** `on_node_click`/`on_edge_click` `$:` expressions run against the RAW node (root-level `node.type`), not the style projection. Use `node.type` in click handlers and `node.data.type` in `node_style`/`edge_style`.
- **Charge falloff:** `charge_max_distance` affects only repulsion, not link/center/alignment forces.
- **Fills:** raw hex, factory-owned category palette (not theme tokens); a per-node `data.fill` overrides the resolved `node_style.fill`. Component chrome (grid, selection ring) uses theme.
- **Modal ids:** `open:` must be string id only; inline object form rejected.
- **Hemisphere split is a render port, not a force:** the in/out hemisphere docking comes from `bundle_links` (perimeter ports off each node's flow axis) — it does **not** need `bundle_directional_force`. With lanes, set `bundle_directional_force: false` (it defaults `true` when `bundle_links` is on): the force rotates nodes and fights `lane_x` for the laned axis's authority. The directional force is for un-laned flow graphs that want the sim to actively orient nodes. In the factory editor it is off (lanes own the layout).
