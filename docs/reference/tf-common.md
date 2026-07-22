# tf-common — the agent runtime: primitives, pub/sub, main loop, errors, logging, debugging

## Import

```python
import teenyfactories as tf
```

## Choosing the right primitive

| Use case | Use |
|---|---|
| React when a row enters a state (pipeline step completes, item arrives) | `tf.on_state(collection, state).do(handler)` |
| React after a delay if the row is still in a state | `tf.on_state(collection, state).delay(seconds=N).do(handler)` |
| Insert a new row (auto-UUID key) | `tf.collection(name).add(state, data={...})` |
| Upsert / update an existing row by key | `tf.collection(name).set(key, state=..., data=...)` |
| Look up one row | `tf.collection(name).get(key)` |
| List rows in a state | `tf.collection(name).state('X').get_all()` |
| Count rows in a state | `tf.collection(name).state('X').count()` |
| Filter rows on a payload predicate | `tf.collection(name).where("field == 'x'").get_all()` |
| Vector similarity search | `tf.collection(name).state('X').vector_search(text_or_vec).limit(5).run()` |
| Run a periodic job | `tf.on_schedule.every(10).minutes.do(fn)` |

One pub/sub primitive: `tf.on_state`; no message bus (detail below). Collection read/write detail (filters, vector search, upsert semantics): see tf-data.

## Pub/Sub Model

All factory data lives in `factory_data`. Every row carries a **state**
column, and a subscribed `(collection, state)` is the queue (full contract
below). There is no separate message bus, no `_messages` collection
shorthand, no `send_message` / `on_message`. To chain stages, write the
next state; to signal an event, write a domain row at a state a consumer
subscribes to.

### Subscribe to a state on a collection (the only primitive)

```python
@tf.on_state('documents', 'loaded').do
def handle_loaded(item):
    doc = item['data']
    # ... do work, then transition state to move the item forward
    tf.collection('documents').set(item['key'], state='chunked')
```

**Two subscription shapes — that's the whole API.**

```python
# Default — process every row in (collection, state).
@tf.on_state('orders', 'submitted').do
def handle(item):
    process(item)
    tf.collection('orders').set(item['key'], state='approved', data=item['data'])

# Delayed — defer dispatch until N seconds after the row entered the state (its state_changed_at).
@tf.on_state('orders', 'submitted').delay(seconds=30).do
def escalate_if_still_submitted(item):
    notify_ops(item)
```

**Contract — the pipeline-FIFO + handler-must-transition + 5-strike-park rule:**

Subscribing to `(collection, state)` means *"process every row currently in that state, oldest first (FIFO, ordered `state_changed_at ASC, key ASC`)."* The state itself is the queue — there is no cursor. **Your handler MUST move the trigger row out of its input state on success** — transition via `tf.collection(...).set(key, state='next', data=...)` or delete via `.remove(key)`. A row left in its input state (handler returned without transitioning, or an aggregator that only reads and writes elsewhere) is NOT consumed: it re-fires every poll and parks after 5 attempts. If a handler's job is to aggregate/read many rows rather than advance one, it belongs on a schedule (`tf.on_schedule`), not an `on_state` subscription.

**One handler per `(collection, state)` — subscriptions are UNIQUE across the whole factory.** Exactly one `.do` handler may subscribe any given `(collection, state)`. Do NOT wire two handlers (in the same agent OR in two different agents) to the same state:

- *Same process (one agent, two `.do`s):* strike/retry accounting is per-row, not per-handler, so both handlers duplicate-execute the succeeding one — core logs a loud `[WARN]` at registration.
- *Different agents/processes:* each dispatch is wrapped in a claim (`try_claim`/`release_claim`, see the claims layer), and **only ONE worker can claim each row**. Which agent's handler wins is non-deterministic; the loser silently skips. There is no cross-process warning — each process only sees its own subscriptions — so this stays invisible until rows go missing.

To have several things happen when a row reaches a state, **fan the work out to DISTINCT next-states**: one handler advances `Order: Paid` → writes `Order: Invoiced` AND `Order: Shipped` (or a fan-out collection), and a separate handler subscribes each of those. Never model "two workers on one queue" by double-subscribing one state. `check_all` reports any `(collection, state)` subscribed by 2+ agents as an ERROR; `check_python` flags a single agent that subscribes one state twice.

A row that stays in the state — whether the handler **raised** OR **returned cleanly without transitioning** (both are counted identically) — is re-dispatched on the next poll. After **5 such non-departures** the row is **parked**: skipped silently until the process restarts, with one `[ERROR]` log:

```
Giving up after 5 attempts; row parked in {collection}.{state} key=...: <reason>
```

Each failed attempt also logs `Handler {collection}.{state} failed on key=... attempt n/5: <err>`. And on the **first re-sighting of a clean no-op** — a handler that ran, returned without error, and left the row exactly where it was (same state AND same `state_changed_at`) — core emits an immediate `[WARN]`, so a stuck handler surfaces on the very next poll instead of only at the 5-strike park:

```
Handler {collection}.{state} returned without advancing key=... (state and state_changed_at unchanged) — it will re-fire and park after 5 attempts...
```

(No such warning for a handler that **raised** — that already logs an `[ERROR]` — or for a **claim-skip**, where the handler never ran.)

Strike accounting is **in-memory and per-row**, keyed `(key, state, state_changed_at)`:

- A **process restart** wipes the strike map → the row is re-attempted. This is intentional: a restart implies a fix was shipped.
- A **genuine re-queue** — a state transition, or an explicit same-state re-stamp that bumps `state_changed_at` (the value both the poll and the strike map key on) — is a new strike key → the count resets to fresh work. A data-only write that leaves `state_changed_at` unchanged does NOT reset it: the row keeps its strike key and still parks.
- Insertion-order-capped at 2048 simultaneously-failing rows; healthy operation keeps it near-empty.

No per-row "safety re-fire" for idempotent aggregators — use `tf.on_schedule` instead of relying on re-dispatch.

| Method | Effect |
|---|---|
| `.do(handler)` | Register the handler. Required. |
| `.delay(seconds=N, minutes=N, hours=N)` | Defer dispatch until `state_changed_at + delta <= NOW()`. Strict cancellation — if the row leaves the watched state before the delay elapses, the handler is skipped. Re-arm — on transition out + back in, `state_changed_at` bumps and the delay restarts. Time units are additive within a single call: `.delay(seconds=30, minutes=2)` → 2m30s. |
| `.claim_duration(seconds=N, minutes=N, hours=N)` | How long this subscription's claim on a row stays valid if the worker dies mid-handler (default 1 hour). If the claim expires, another worker can pick the row up. Time units are additive within a single call. |

**The poll scan.** Dispatch is a plain FIFO scan of the state — no cursor, no re-fire tracking in SQL (the strike map handles that in memory):

```sql
-- live (non-delayed) handler
SELECT factory_name, collection, key, user_id, value, state, created_at, updated_at
  FROM factory_data
 WHERE factory_name = $factory AND collection = $collection AND state = $state
 ORDER BY state_changed_at ASC, key ASC
```

The `.delay()` variant (same state-as-queue semantics; cancellation/re-arm per the table row above) adds one predicate:

```sql
-- delayed handler — same scan plus the delay floor
   AND state_changed_at + ($delay_seconds * INTERVAL '1 second') <= NOW()
```

Granularity = your `run_pending()` cadence. Live and delayed handlers for the same `(collection, state)` interleave by natural `state_changed_at` order in one inline pass.

**Poll-based dispatch, NOTIFY-gated.** Dispatch is **always** poll-based — NOTIFY (see *NOTIFY channels* below) never delivers or routes work, only wakes the poll. Each `run_pending()` tick drains the NOTIFY buffer and runs a poll pass only when:

- an own-factory NOTIFY was drained this tick (payload's `factory_name` matches), **OR**
- 10 s (`_SAFETY_POLL_INTERVAL_SEC`) elapsed since the last poll (a hard floor — never more often absent a NOTIFY), **OR**
- it is the first tick.

Otherwise `run_pending()` issues **zero queries**. Cross-factory NOTIFYs on the shared channel are drained (so the buffer can't grow) but do not trigger a poll. There is no per-state channel, no client-side channel hashing, no dedupe LRU, no "safety poll caught N rows" warning — none of that exists.

Observed dispatch latency is your own `tf.sleep(N)` loop cadence in the main loop (see *Main Loop* below), not a core concern; there is no per-second DB hammering.

### Row shape

Every `on_state` handler receives the same row dict:

```python
{
    'factory_name': 'my_factory',
    'collection':   'documents',
    'key':          'abc123',
    'user_id':      'system',
    'data':         {...},          # JSONB payload (the surface key is `data`)
    'state':        'loaded',
    'state_changed_at': datetime(...),
    'created_at':   datetime(...),
    'updated_at':   datetime(...),
}
```

`data` is always a dict (defaults to `{}` if the row was inserted with no payload). The DB column is named `value` for legacy reasons; the Python surface uses `data` everywhere.

### NOTIFY channels

There is **one** wake channel for agent dispatch: `tf_data_changed`. It is emitted by the `factory_data` NOTIFY trigger on every write, with a JSON payload that includes `factory_name`. tf core issues a single global `LISTEN tf_data_changed` and treats any own-factory fire purely as an **advisory "poll now" wake** — it never delivers or routes work, and the only payload field core consults is `factory_name` (collection/state for the actual work come from the poll query, not the payload).

No per-state channel, no client-side hashing (as above) — concretely, plaintext `{factory}.{collection}.{state}` channels and `tf_state_<md5(...)>` channels are not emitted and not subscribed.

| Channel | Length | Fires when | Consumer |
|---|---|---|---|
| `tf_data_changed` | 15 | every `factory_data` INSERT/UPDATE/DELETE | tf core (advisory poll wake, `factory_name`-filtered); orchestrator `eventBus` → SSE → UI (`useBoundData`) |
| `tf_logs_changed` | 15 | every `factory_logs` INSERT | orchestrator `eventBus` → SSE → log streams |

**Payload shape (metadata only, never carries `value`):**

```json
{
  "factory_name":  "my_factory",
  "collection":    "documents",
  "key":           "doc-123",
  "op":            "insert",          // "insert" | "update" | "delete"
  "state_before":  null,
  "state_after":   "loaded",
  "state_changed": true,
  "user_id":       null,
  "ts":            "2026-05-09T05:36:00Z",
  "_size_hint":    "small"            // "small" | "large"
}
```

`_size_hint` is `'large'` when `octet_length(NEW.value::text) > 6000`. Consumers seeing `'large'` should NOT expect the row body inline — fetch via `GET /api/factories/:factory/data/:collection?since=<ts>` and reconcile; `'small'` just deprioritises that fetch.

Both channels share the same payload shape. Factory authors never deal with channels directly — `tf.on_state` handles the `LISTEN`/poll wiring for you.

**Naming hygiene**: factory and collection names are validated `^[a-z][a-z0-9_-]{0,29}$` at create time (URL/dataRef/log-line/chat-tool sanity). **At runtime, tf additionally validates every collection AND state identifier** — the args to `tf.collection(name)` / `tf.on_state(collection, state)` and every `state=` / `.state(...)` value — against `^[a-z0-9_]+$` (≤40 chars), raising `ValueError` on the first offending call (note: a **hyphen** passes the create-time check but fails here, as do capitals and spaces). factory.yml carries the Title-Case `"Collection: State"` **display label**; agent code and UI bindings use the lowercase **runtime slug** (`"Email: Sent"` → `tf.on_state('email', 'sent')`, `tf.collection('email')`, `state='sent'`). Mapping rule + worked example: see factory-yaml. `check_python` flags Title-Case/spaced string literals in these positions before deploy.

There is no message-bus primitive — pure aggregation/summarisation is a scheduled recompute (`tf.on_schedule`) overwriting a fixed-key stats row, never re-dispatch on an unchanged row. **One handler per `(collection, state)`** — a second registration emits a loud `[WARN]` (strike accounting is per-row, so two handlers would silently duplicate-execute the succeeding one).

## Scheduling

```python
tf.on_schedule.every(10).minutes.do(job_function)
tf.on_schedule.every().hour.do(job_function)
tf.on_schedule.every().day.at("10:30").do(job_function)
tf.on_schedule.every().monday.do(job_function)
```

## Main Loop

Every agent script must end with:
```python
while True:
    tf.run_pending()
    tf.sleep(1)
```

`tf.run_pending()` takes no arguments. Each tick it: flushes any subscriptions registered since the last tick, runs scheduled jobs, drains the NOTIFY buffer, and runs a poll pass **if due** (own-factory NOTIFY drained, OR 10 s elapsed since the last poll, OR first tick) — otherwise it issues zero queries. The first call also bootstraps the lifecycle (opens the connection, `LISTEN tf_data_changed`, installs SIGTERM/SIGINT handlers, publishes the MCP catalog, forces a first poll).

`tf.sleep(N)` is the documented sleep primitive — externally a blocking N-second sleep, internally polling a shutdown flag at 1 s granularity so SIGTERM/SIGINT preempts it. NOTIFYs that arrive while sleeping buffer on the connection and are observed on the next tick. Your `tf.sleep(N)` cadence is the floor on dispatch latency.

### Shutdown semantics

Agent containers terminate cleanly when the orchestrator sends SIGTERM (and when the operator hits Ctrl-C in dev). `tf.run_pending()` installs SIGTERM + SIGINT handlers on its first call — **agent code does NOT install signal handlers itself**.

When a signal arrives:

1. The handler flips an internal flag and logs one line (`SIGTERM received, shutting down after current tick`); repeated signals don't re-log.
2. At the end of the current `tf.run_pending()` tick — OR at the next 1 s slice inside `tf.sleep(N)` — the flag is observed and `sys.exit(0)` raises `SystemExit` out of your `while True`. Python then runs normal cleanup (atexit hooks, generator finalisers, context-manager `__exit__`).

The loop shown above needs no code change to get clean shutdown.

Trade-offs:

- **In-flight LLM calls are not interrupted** — a handler mid-`tf.call_llm(...)` finishes (or gets SIGKILL'd by Docker after the grace period); poll `tf.shutting_down()` at safe checkpoints to bail early.
- **No `tf.run_forever()` primitive** — loop shape preserved, wiring is internal.
- **No `stop_grace_period` config change** — standard Docker/Kubernetes defaults apply.

Optional surface — `tf.shutting_down() -> bool` — for handlers that want to cooperate with shutdown:

```python
@tf.on_state('docs', 'loaded').do
def handle(item):
    for chunk in big_iter(item):
        if tf.shutting_down():
            return                          # row stays in 'loaded'; re-tried on restart
        process(chunk)
    tf.collection('docs').set(item['key'], state='processed', ...)
```

Off-main-thread: `signal.signal` only works on the main thread — if `run_pending` runs on a worker thread, handler installation is silently skipped, so the main loop should always live on the main thread.

## Error Handling Policy

One policy, applied everywhere in `tf.*` and in factory code.

### 1. Let exceptions propagate by default

Don't `except Exception: return None` to hide failures. The worker event loop (`run_pending`) catches per-handler exceptions so one bad message doesn't kill the agent — that's the ONLY silent-catch boundary.

Anywhere else: raise. If Postgres is down, callers need to know, not silently see `[]` and skip work.

### 2. Reads return `Optional[T]` only for "not found"

```python
row = tf.collection('documents').get('missing_key')   # → None (not found)
```

`None` / `[]` from a read means "the row(s) don't exist" — never "the query failed." If you see a read returning empty and you're not sure whether data exists, check with `state(…).get_all()` or `state(…).count()`.

### 3. Writes return the identifying key

```python
key = tf.collection('chunks').add(state='new', data=value)   # auto-uuid key returned
tf.collection('chunks').set('known_key', data=value)         # returns 'known_key'
```

No booleans. If the write fails, it raises.

### 4. Strike / park: a non-departing row is retried 5 times, then parked

Same rule as in *Pub/Sub Model* above (raised or silently-non-transitioning are counted identically as one strike; 5 strikes parks the row; restart or a genuine rewrite resets the count) — this is the error-handling consequence of that FIFO-queue contract, not a separate mechanism.

**Slow-failing handlers block the queue.** A single inline FIFO pass dispatches the state in order, so a handler that hangs (or fails slowly) on the row at the head delays every fresher row behind it — for up to 5 ticks before that head row parks. Handlers that do network/file I/O **must set their own timeouts**; core does not impose one (see *Shutdown semantics* above — `tf.sleep`'s polling doesn't interrupt blocking work inside a handler).

### 5. Handlers should log and re-raise on unexpected errors

Inside an `on_state` handler, if you catch an exception to add context, log it and re-raise (the raise still counts as a strike — see item 4):

```python
@tf.on_state('documents', 'loaded').do
def handle_loaded(item):
    try:
        process(item)
        tf.collection('documents').set(item['key'], state='processed')  # consume the row
    except FileNotFoundError:
        # Known/expected condition — transition out so it doesn't strike
        tf.collection('documents').set(item['key'], state='missing_file')
    except Exception as e:
        tf.log_error(f"Unexpected failure on {item['key']}: {e}")
        raise      # let run_pending's per-handler catch record it; this is one strike
```

### 6. No "first call auto-connects" guards in hot paths

Provider objects connect once at module init. Per-call `if not self.connection: self.connect()` patterns hide connection-state bugs and add branching to every call.

## Logging

All five logging functions share the same signature: `(message: str)`. No `level=` kwarg, no `metadata=` kwarg.

```python
tf.log_debug("Detailed debug info")
tf.log_info("General status message")
tf.log_warn("Warning condition")
tf.log_error("Error occurred")
tf.log_persona("First-person message for UI speech bubbles")
```

`log_persona` writes a separate `level='persona'` row to `factory_logs` so the UI can render it as a chat bubble; otherwise it behaves like `log_info`.

## Stepped debugging

```python
tf.breakpoint("about to do the risky thing")   # halt this agent until the operator clicks Continue
```

`tf.breakpoint(message)` is a single-call halt — **a cheap no-op when the factory's debug mode is off**, safe to leave in production code. When on, it writes a `level='breakpoint'` row to `factory_logs` and blocks this agent until the operator clicks **Continue** in the logs panel (or disables debug mode, which auto-releases every halted breakpoint).

Per-factory debug mode is toggled from the UI (factory header → **Debug**). There are two scopes:

- **All jobs** — agents halt before *every* `(collection, state)` handler dispatch, every `tf.on_schedule` job invocation, *and* every `tf.breakpoint()` call — step through queue + scheduled work one job at a time.
- **`tf.breakpoint()` only** — auto pre-dispatch halts are skipped; only explicit `tf.breakpoint()` calls fire — for stepping inside one handler without halting on every job.

Mode lives in `factory_data._debug.mode` (state column = the scope: `'all'` | `'explicit'` | `'disabled'`). The `_debug` collection is reserved — never write rows in it from agent code; only `tf.breakpoint()` and the orchestrator's debug endpoints touch it.

Halt state lives on each `factory_logs` breakpoint row: `log_data._debug.state` is `'waiting'` until the orchestrator flips it to `'continued'`. The agent polls its own row every second.

```python
@tf.on_state('orders', 'received').do
def handle(item):
    order_id = item['key']
    tf.log_info(f"validating {order_id}")
    tf.breakpoint(f"about to charge card for {order_id}")  # halts here when scope is 'all' or 'explicit'
    charge_card(item['value'])
```

Don't include secrets in the message — it's written to `factory_logs` and visible to anyone with logs-read access.

## Time and IDs

```python
tf.get_timestamp()      # local-time ISO-8601 string honouring $TZ ($TZ defaults to UTC)
tf.get_timestamp_utc()  # UTC ISO-8601 string
tf.generate_unique_id() # UUID hex string
```

Both timestamp functions return tz-aware ISO-8601 strings. The orchestrator forwards `TZ` to every agent container so timestamps match the deployment locale without per-factory config.

## Public API surface

The complete list of names exported from `import teenyfactories as tf`:

| Group | Names |
|---|---|
| Versioning | `__version__` |
| Logging | `log_debug`, `log_info`, `log_warn`, `log_error`, `log_persona` |
| Time / IDs | `get_timestamp`, `get_timestamp_utc`, `generate_unique_id` |
| LLM | `llm` (fluent builder — `.ask` / `.ask_with_meta` / `.run_agent_loop` / `.run_agent_loop_with_meta`), `call_llm` (LEGACY) |
| Secrets | `secrets` |
| Bucket store (file volumes) | `bucket_store`, `BucketStoreError`, `BucketNotFoundError`, `BucketPermissionError`, `BucketConflictError` |
| Pub/sub | `on_state`, `run_pending` |
| MCP | `add_mcp_server`, `add_mcp_tool` |
| Data | `collection`, `embed` |
| Scheduling | `on_schedule` |
| Lifecycle | `sleep`, `shutting_down` |
| Debugging | `breakpoint` |
| Config | `FACTORY_NAME`, `AGENT_NAME`, `AGENT_SLUG`, `AGENT_ID` |

If a symbol is not in this list, it is not part of the supported surface. Don't reach into submodules. Detail: LLM → tf-llm; bucket store/volumes → tf-volumes; MCP tools → tf-mcp; env vars → tf-environment.

## Agent file conventions

Every agent `.py` file MUST start with a docstring of this exact shape:

```python
"""
Agent: {Name}

Purpose: {brief}
Triggers: {what wakes this up — states, messages, schedules}
Outputs: {what it produces — states it transitions, topics it sends}
"""
```

This is non-optional — the orchestrator surfaces it in the agent listing and the chat agent reads it to understand factory topology.

There is no agents-vs-workers distinction. Every factory component is an "agent". Some call LLMs (`tf.call_llm`), some don't — that's a property of what the code does, not a structural category. The folder layout is `factory.yml` + `agents/*.py`. No `workers/`, no `common/`.

## Debugging

Tail logs, inspect state, or listen on a NOTIFY channel against the orchestrator's Postgres. The queries below matter; reaching the `psql` prompt depends on your deployment backend:

- Compose: `docker exec -it teenyfactories-postgres psql -U teenyfactories -d teenyfactories`
- Kubernetes: `kubectl exec -it <postgres-pod> -- psql -U teenyfactories -d teenyfactories`
- Managed Postgres / port-forward: any local `psql` pointed at the orchestrator's DB URL.

In the `psql` session:

```sql
-- Tail a factory's logs (filter by agent slug, not display name —
-- service_name stores AGENT_SLUG, which is stable across renames):
SELECT created_at, service_name, level, left(message, 160) FROM factory_logs
 WHERE factory_name='my_factory' ORDER BY id DESC LIMIT 50;

-- Tail one agent's logs:
SELECT created_at, level, left(message, 160) FROM factory_logs
 WHERE factory_name='my_factory' AND service_name='my_agent_slug'
 ORDER BY id DESC LIMIT 50;

-- Inspect factory_data state (DB column is still `value` — read it as `data` in Python):
SELECT collection, state, key FROM factory_data
 WHERE factory_name='my_factory' ORDER BY updated_at DESC LIMIT 20;

-- Inspect a (collection, state) FIFO queue directly — these are the rows
-- tf.on_state would dispatch, in the same order (oldest first):
SELECT key, left(value::text, 80), state_changed_at FROM factory_data
 WHERE factory_name='my_factory' AND collection='documents' AND state='loaded'
 ORDER BY state_changed_at ASC, key ASC;

-- LISTEN on the single wake channel tf.on_state uses (advisory poll wake;
-- payload carries factory_name, not the row body):
LISTEN tf_data_changed;

-- LISTEN on the logs firehose:
LISTEN tf_logs_changed;
```

## Agent Script Template

```python
"""
Agent: {Name}

Purpose: {What this agent does}
Triggers: {What states/messages/schedules trigger it}
Outputs: {What collections/states/topics it writes}
"""

import teenyfactories as tf
from pydantic import BaseModel, Field
from langchain_core.prompts import PromptTemplate


class MyResponse(BaseModel):
    result: str = Field(description="Description")


PROMPT = "Analyze: {input}\n{format_instructions}"


@tf.on_state('inputs', 'loaded').do
def handle_input(item):
    tf.log_persona("Processing...")

    prompt = PromptTemplate.from_template(PROMPT)
    response = tf.call_llm(prompt, {"input": item['data']}, response_model=MyResponse)

    # Move the input forward and emit a result row.
    tf.collection('inputs').set(item['key'], state='processed')
    tf.collection('results').add(state='ready', data={'result': response.result})


tf.log_info("Starting {Name}")

while True:
    tf.run_pending()
    tf.sleep(1)
```

## Key Patterns

- `tf.on_state(collection, state).do(handler)` (optionally `.delay(seconds=N)`) is the only pub/sub primitive; handlers MUST transition or `.remove()` the row, per the FIFO contract above. Don't bypass the `tf` surface for writes — see tf-data for filter/vector-search detail.
- Each agent runs as a separate container instance scheduled by the orchestrator's deployment backend. The agent script is made available inside the container at `/app/script.py` (the exact mechanism — bind mount, ConfigMap, baked layer — is backend-specific and not something agent code should depend on).
