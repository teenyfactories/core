# tf-data — collections: writes, reads, queries, vector search, REST

Every row carries a `state`, a `user_id`, and audit timestamps. The state drives pub/sub — downstream workers subscribe to state transitions via `tf.on_state(collection, state)`.

**Reserved collection names** (do not write from agent code): `_debug` (stepped-debug toggle, see *Stepped debugging* below), `_mcp_*` (MCP server registrations). The leading underscore is the convention — keep your own collection names underscore-free.

## Writes

```python
# UPSERT by key. At least one of state= / data= / embedding= is required.
# On INSERT, omitted state defaults to 'new', omitted data defaults to {}.
# On UPDATE, each argument you pass replaces that column outright; arguments you
# omit are left untouched. There is NO field-level merge inside the JSONB:
# passing data= REPLACES the ENTIRE value blob (state= likewise replaces state).
tf.collection('documents').set('doc-123', state='loaded', data={'title': 'Q4 plan'})

# State-only transition: omit data= entirely to keep the existing value blob and
# only move the row's state. This is the one case that preserves the payload.
tf.collection('documents').set('doc-123', state='chunked')

# Data write: passing data= OVERWRITES the whole value. This drops every field
# not present in the dict you pass — 'title' here would wipe any sibling keys.
tf.collection('documents').set('doc-123', data={'title': 'Q4 plan v2'})

# INSERT a new row with an auto-generated UUID key. State is required.
new_key = tf.collection('chunks').add(state='new', data={'text': 'content'})
```

**Whole-value replace — spread to update safely.** `data=` is not a patch. Because it replaces the entire `value` JSONB, a handler that advances a row while setting one field MUST spread the existing payload, or every other field is nuked:

```python
# CANONICAL safe-update idiom: spread the current data, then override.
tf.collection('documents').set(item['key'], state='processed',
                               data={**item['data'], 'analysed': True})

# WRONG — replaces the whole blob with a single key, destroying everything else.
tf.collection('documents').set(item['key'], state='processed',
                               data={'analysed': True})
```

The one documented exception is a **state-only write** (no `data=` at all), which preserves the existing value and only transitions state — no spread needed there.

> **`tf.collection(...).set()` and the REST `PUT` differ on data writes.** The Python `set(key, data=...)` **replaces** the whole `value` blob (spread to preserve siblings, as above). The orchestrator's HTTP write path — `PUT /api/factories/:name/data/:collection/:key` (the composable-UI write path) — instead **shallow-merges** the JSONB (`value = existing || patch`, a top-level key merge; nested objects are still replaced wholesale, not deep-merged). So a UI patch of `{"approved": true}` keeps the row's other top-level fields, whereas the same one-key dict passed to `set(data=...)` would wipe them. State-only and state+data variants of the PUT are covered under *Orchestrator REST surface* below.

## Reads — the lazy query builder

`tf.collection(name)` returns a lazy query builder. **Filters chain and AND together; terminals execute.** Nothing hits the DB until you call a terminal.

```python
row      = tf.collection('documents').get('doc-123')             # full row dict, or None — point lookup
exists   = tf.collection('documents').exists('doc-123')          # bool — point lookup

all_rows = tf.collection('documents').get_all()                  # every row, newest first
loaded   = tf.collection('documents').state('loaded').get_all()  # filtered by state
n        = tf.collection('documents').state('loaded').count()    # int
ready_or_done = tf.collection('documents').state(['ready', 'done']).get_all()   # state IN (...)
```

**Filters (chain, AND together):**

| Filter | Effect |
|---|---|
| `.state('X')` | `state = 'X'` |
| `.state(['X', 'Y', 'Z'])` | `state IN ('X', 'Y', 'Z')` |
| `.where("<DSL string>")` | a payload/column predicate (grammar below). Multiple `.where(...)` AND together. |

Calling **`.state()` twice raises `ValueError`** — combine the values into one list instead of chaining two `.state(...)` calls.

**Terminals (execute the query):**

| Terminal | Returns |
|---|---|
| `.get_all()` / `.run()` (alias) | `list[row dict]`, ordered `updated_at` descending — same row shape handlers receive |
| `.count()` | `int` |
| `.first()` | first row dict, or `None` |
| iteration (`for row in tf.collection(...).state(...)`) | iterates the rows |

`.vector_search(text_or_vec)` is a **filter, not a terminal** — it sets ANN ordering and you still call a terminal (`.run()` / `.get_all()`) to execute. See *Vector search* below.

```python
# chained: state filter + payload predicate + terminal
tf.collection('chunks').state('vectorised') \
    .where("document == 'ae400398.pdf' and token_count >= 400") \
    .get_all()

n = tf.collection('chunks').state(['vectorised', 'chunked']).count()
```

### The `.where()` string DSL

`.where("...")` takes a small predicate language over a row's JSONB payload and (whitelisted) row columns. The string is **parsed and parameterized** — values are bound as SQL parameters, never concatenated into the query.

**Operators:** `== != < > <= >= in "not in" and or not ( )`.
**Literals:** string (single- or double-quoted, no backslash escapes), number, bool (`true`/`false`), list `[a, b, c]`.

**Field namespaces** — three ways to name a field, because the JSONB payload and the lifecycle columns can collide:

| Reference form | Resolves to | Example |
|---|---|---|
| bare field | JSONB payload key (`value->>'field'`) | `document == 'x'` |
| `data.field` | explicit payload alias — same as bare, disambiguates | `data.state == 'VIC'` |
| `meta.<col>` | a row **column** (whitelisted) | `meta.created_at > '2026-01-01'` |

`meta.*` whitelist: **`state`, `key`, `user_id`, `created_at`, `updated_at`, `state_changed_at`**. `factory_name` and `collection` are **not** addressable — they're pinned by the collection scope. Any unknown `meta.*` column, or any unknown namespace prefix, is a parse error.

**The collision rule (this is *why* the namespaces exist).** Consider an address collection where the payload has its own `state` field for the Australian state:

```python
tf.collection('address').add(state='new', data={'suburb': 'Geelong', 'state': 'VIC'})

# bare `state` reads the PAYLOAD field:
tf.collection('address').where("state == 'VIC'").get_all()        # payload state == 'VIC'

# meta.state reads the LIFECYCLE column:
tf.collection('address').where("meta.state == 'new'").get_all()   # row's lifecycle state
```

Bare `state` → payload `value->>'state'`. The lifecycle column is always `meta.state`. (For filtering on the lifecycle state you'd normally use the `.state(...)` filter; `meta.state` exists for the rare case you want it inside a compound `.where(...)`.)

**Numeric / bool casting.** Ordering operators (`< > <= >=`) and numeric/bool literals cast the JSONB text (`::numeric` / `::boolean`) with a guard, so a row whose payload value isn't a valid number/bool is **excluded** from the result rather than erroring the whole query. String comparisons (`==`, `in`) don't cast. `!=` is compiled to `IS DISTINCT FROM` (so a NULL payload field is correctly *not equal* to a value).

```python
tf.collection('chunks').where("token_count >= 400 and token_count < 1200").get_all()
tf.collection('invoice').where("paid == true").count()
```

**OR and grouping** are supported:

```python
tf.collection('lead').where("score >= 80 or (tier == 'gold' and active == true)").get_all()
```

**Injection / untrusted-input note.** Because `.where()` strings are parsed and parameterized (values bind as parameters, never string-concatenated into SQL), building a predicate with an f-string is **safe against SQL injection**:

```python
name = item['data']['filename']
tf.collection('chunks').where(f"document == '{name}'").get_all()   # parameterized — injection-safe
```

However, if `name` is **untrusted end-user input**, parameterization only protects the SQL layer — confining a query to within-factory confidentiality (i.e. not letting a user craft a predicate that surfaces rows they shouldn't see) is the **factory author's responsibility**. Validate/scope untrusted predicate inputs yourself.

**Not yet built** (don't reach for these — they're deferred): `.order_by(...)`, `.min_similarity(...)`, per-group top-N (`top_per`), and `.delete()`. (`or` / grouping *is* built — it's listed above.)

## Delete

```python
tf.collection('chunks').remove('chunk-xyz')   # cascades to factory_vectors
```

There is no bulk-delete. If you need to delete many rows, iterate and call `remove` per key — that's intentional friction.

## Vector search

`.vector_search(text_or_vec)` is a **chainable filter, not a terminal** — it sets ANN (cosine) ordering on the query. You still call a terminal (`.run()` / `.get_all()`) to execute, and `.limit(n)` to cap the result. It composes with `.state(...)` and `.where(...)` like any other filter.

```python
# Pass a string — auto-embedded via tf.embed — then cap and run.
hits = tf.collection('chunks').vector_search('budget overrun').limit(5).run()

# Or pass a pre-computed vector.
hits = tf.collection('chunks').vector_search(query_vector).limit(5).run()

# Compose with state and payload filters.
hits = tf.collection('chunks').state('ready') \
    .where("document == 'ae400398.pdf' and token_count >= 400") \
    .vector_search('budget overrun').limit(5).run()

# Each hit is a row dict with an extra `similarity` key (cosine, 0..1):
#   {factory_name, collection, key, user_id, data, state, created_at, updated_at, similarity}
```

**`.vector_search()` with no `.limit()` defaults to a limit of 10** — an ANN search is always bounded; the default keeps an unlimited scan from coming back accidentally.

### State filtering via the builder

Reads filter by state via the `.state(...)` filter on the builder — the `state=` keyword argument is never used. State filtering is always declarative through the builder chain:

```python
tf.collection('c').state('x').get_all()
tf.collection('c').state('x').count()
tf.collection('c').state('x').vector_search(q).limit(5).run()
```

## Embedding-aware writes

```python
vector = tf.embed("chunk text")
tf.collection('chunks').set(
    'chunk-1',
    state='ready',
    data={'text': 'chunk text'},
    embedding=vector,                 # routed to factory_vectors / dim column
)
```

The vector's dimension must match one of the fixed `factory_vectors` sizes or the write fails — see **tf-llm § Embeddings** (Dimension constraint) for the list and supported models.

The `user_id` column defaults to `'system'` for agent writes. Backend API writes stamp the session's user id (fallback `'-1'` until auth is wired).

## Orchestrator REST surface for `factory_data`

The orchestrator exposes the same `factory_data` rows over HTTP for UI consumers. Agent-side Python code uses `tf.collection(...)` directly; UI code uses these endpoints (and is the canonical client of `useBoundData`).

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/factories/:name/data/:collection` | List rows in a collection. Paginated, sortable, filterable by state, filterable by recency via `?since=`. |
| `GET` | `/api/factories/:name/data/:collection/stats` | Aggregate stats (group-by JSONB field, or daily counts over N days). |
| `GET` | `/api/factories/:name/data/:collection/:key` | Single row by key. |
| `PUT` | `/api/factories/:name/data/:collection/:key` | Upsert one row. Body: `{data?, state?}` (at least one required). **Shallow-merges** `data`. |
| `DELETE` | `/api/factories/:name/data/:collection/:key` | Delete one row by key. |
| `GET` | `/api/factories/:name/_state_counts` | `[{collection, state, count}]` across all user-facing collections (excludes `_`-prefixed reserved collections). |

### `PUT /api/factories/:name/data/:collection/:key` — upsert a row (shallow-merge)

The HTTP write path used by the composable UI. Body must include `data`, `state`, or both (else `400`). **This is the one write path that does NOT replace the whole value blob** — on an existing row it **shallow-merges** the JSONB (`value = existing || patch`, a top-level key merge):

| Body | On new row | On existing row |
|---|---|---|
| `{data, state}` | inserts `value = data`, `state = state` | `value = existing ‖ data` (top-level merge), `state` overwritten |
| `{data}` only | inserts `value = data`, `state = 'new'` | `value = existing ‖ data`; **state preserved** |
| `{state}` only | inserts `value = {}`, `state = state` | `state` overwritten; **value preserved** |

`‖` is a **top-level** merge (Postgres `jsonb ||`): keys in `data` overwrite same-named top-level keys; keys absent from `data` survive; a nested object under a shared key is replaced wholesale (no deep merge). Every PUT stamps `user_id` from the session. Contrast the Python surface: `tf.collection(...).set(key, data=...)` **replaces** the whole blob (spread to preserve siblings).

### `GET /api/factories/:name/data/:collection` — list rows

**Query params**

| Param | Type | Default | Notes |
|---|---|---|---|
| `state` | string | (none) | Filter rows by state. |
| `page` | int ≥ 1 | `1` | Ignored when `since` is set. |
| `page_size` | int 1..1000 | `50` | Ignored when `since` is set. |
| `sort_field` | JSONB key matching `[a-zA-Z_][a-zA-Z0-9_]{0,63}` | `updated_at` | Strict allowlist — anything else 400s. |
| `sort_dir` | `asc` \| `desc` | `desc` | Forced to `asc` when `since` is set (chronological catch-up). |
| `since` | ISO-8601 timestamp | (none) | Returns rows where `updated_at > since`. See below. |

**Default response shape (no `since`)**

```json
{
  "rows": [{ "key": "...", "data": {...}, "state": "...", "user_id": "...", "created_at": "...", "updated_at": "..." }],
  "page": 1,
  "page_size": 50,
  "total": 312,
  "total_pages": 7
}
```

**`?since=<ISO-8601>` — reconnect catch-up**

Used by the composable `useBoundData` hook to recover from a WebSocket disconnect without throwing away cached state. Caller records the largest `updated_at` it has seen, then on reconnect issues `?since=<that timestamp>` to pick up only rows that have changed since.

Behaviour:

- Rows are filtered by `updated_at > since` (strict greater-than — caller passes the last value it has, server returns everything strictly newer).
- `updated_at` is bumped on every INSERT and every UPDATE (state transition or data write — the `PUT` shallow-merges the JSONB rather than replacing it, but any change still bumps `updated_at`), so `since` catches state-only transitions on existing rows. `created_at` is not used.
- AND-composes with `?state=`. Both filters apply.
- Rows are returned ordered by `updated_at ASC` (oldest first within the window) so the caller can advance its cursor monotonically.
- `page` and `page_size` are ignored. A backstop hard cap of **10000 rows** applies; if the cap is hit, `truncated: true` is returned and the caller should fall back to a full re-fetch (request without `since`).
- The cutoff is echoed back as `since: "<iso>"` so the caller can confirm the server honoured it.

**Response shape with `?since`**

```json
{
  "rows": [...],
  "since": "2026-05-03T12:34:56.000Z",
  "total": 17,
  "truncated": false
}
```

`page` / `page_size` / `total_pages` are omitted from the since-response. `total` is `rows.length`.

**Error path**

- Non-parseable `since` value → `400 {"error": "bad_request", "detail": "since must be ISO-8601 (e.g. 2026-05-03T12:34:56Z)"}`. Validation is `const t = new Date(since); if (isNaN(t.getTime())) reject`.
- Future-dated `since` is allowed (returns empty `rows: []`); server does not clamp.

**Tombstones / deletes — known gap (v1)**

Rows deleted between disconnect and reconnect do not appear in `?since=` results — the caller has no way to know to evict them. v1 accepts this gap. Mitigation: `tf_data_changed` carries `op: 'delete'` for live deletes, so an open SSE connection sees them; on reconnect, the next NOTIFY on the collection (any cause) prompts `useBoundData` to refetch, which catches deletions that happened during the gap. A full deletion-replay (e.g. a soft-delete tombstone column queried by `?since=`) is still deferred — the live channel covers the common case.

**Backwards compat**

Purely additive. Callers without `?since=` get the existing `page`/`page_size`/`total_pages` shape unchanged.
