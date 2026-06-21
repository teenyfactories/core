# The tf module

`teenyfactories` (imported as `tf`) is the Python framework for building
distributed agent systems. You write small, single-purpose **agents**; the
framework gives each one a database-backed pub/sub inbox, an LLM client, an
embedding/vector-search store, a file-volume store, scheduling, and a way to
expose tools to a chat assistant — all through one fluent module.

```python
import teenyfactories as tf
```

Everything an agent needs flows through `tf`. You should not need to import
`psycopg2`, an LLM provider SDK, or reach into `teenyfactories` submodules —
if a name isn't on the [public surface](#public-api-surface), it isn't part
of the supported API.

---

## The factory paradigm

A factory is a folder of agents that cooperate through a shared, state-driven
data store. The whole model rests on one idea:

> **State on a row drives the lifecycle.** Every row of data carries a `state`.
> A subscribed `(collection, state)` pair *is* a FIFO queue. An agent reacts to
> rows in a state, does work, and **transitions the row to the next state**.

There is exactly one pub/sub primitive — `tf.on_state` — and no separate
message bus. To move an item along a pipeline, you write its next state. To
signal an event, you write a domain row at a state some other agent is
watching.

```python
import teenyfactories as tf

@tf.on_state('document', 'loaded').do
def handle_loaded(item):
    text = item['data']['text']
    # ... do the work ...
    # consume the row by moving it out of 'loaded':
    tf.collection('document').set(item['key'], state='chunked')

@tf.on_state('document', 'chunked').do
def handle_chunked(item):
    ...                                  # the next stage picks up from here
```

### Agents are just agents

There is **no agents-vs-workers distinction**. Every component is an "agent".
Some call an LLM (`tf.call_llm`), some only move data around — that's a
property of what the code does, not a structural category. The layout is one
`factory.yml` plus an `agents/` folder with **one Python file per agent**, and
the file's slug is its filename: `agents/{slug}.py`.

Every agent file should open with a docstring describing what it does:

```python
"""
Agent: Document Chunker

Purpose: Split loaded documents into overlapping chunks.
Triggers: document rows entering state 'loaded'
Outputs: document → 'chunked'; chunk rows at 'new'
"""
```

### The main loop

Every agent script ends with the same loop:

```python
while True:
    tf.run_pending()
    tf.sleep(1)
```

`tf.run_pending()` takes no arguments. Each tick it flushes any newly
registered subscriptions, runs scheduled jobs, and runs a poll pass when work
may be waiting. The **first** call bootstraps the agent: it opens the database
connection, starts listening for change notifications, installs graceful
shutdown handlers, publishes any MCP tools, and forces a first poll.

`tf.sleep(N)` is the sleep primitive you should use instead of `time.sleep`.
It behaves like a blocking N-second sleep but checks for a shutdown signal
each second, so the container terminates promptly on `SIGTERM`/`SIGINT`. Your
`tf.sleep(N)` cadence is the floor on how quickly new work is picked up.

!!! note "Startup replay means nothing is lost"
    When an agent starts, `tf.on_state` doesn't only see *future* writes — it
    polls the current contents of every state it subscribes to. Any row that
    piled up in a watched state while the agent was down is dispatched on the
    next poll, oldest first. You never have to reconcile a backlog by hand.

### The strike-and-park contract

Because the state *is* the queue, a row is only "consumed" when the handler
moves it out of the state (transition via `.set(..., state=...)` or delete via
`.remove(...)`). A row still sitting in the state on the next poll counts as a
**strike** — and this is true whether the handler **raised** *or* **returned
cleanly without transitioning the row** (in both cases the work isn't done).

After **5** non-departures the row is **parked**: skipped silently until the
process restarts, with one error logged. A process restart clears the
in-memory strike count (a restart implies a fix shipped); a genuine rewrite of
the row resets it too.

!!! warning "Your handler must move the row out of its state"
    The single most common mistake is a handler that does its work but forgets
    to transition the row. It will be re-dispatched five times and then
    parked. Always end a handler by transitioning or removing the row — or, for
    a known/expected condition, transitioning it to a terminal state of its own
    (e.g. `state='missing_file'`).

---

## Collections and state

`tf.collection(name)` is your handle on a named collection of rows. Collection
names must match `[a-z0-9_]+` (max 40 chars); state names follow the same
rule. Leading-underscore names (`_messages`, `_mcp_*`, `_debug`) are reserved —
don't write them from agent code.

### Writing rows

| Call | Effect |
|---|---|
| `collection(c).set(key, state=, data=, embedding=)` | UPSERT by key. At least one of `state` / `data` / `embedding` required. |
| `collection(c).add(state, data=, embedding=)` | INSERT a new row with an auto-generated UUID key. `state` required. Returns the new key. |
| `collection(c).remove(key)` | Delete one row (cascades to its vector). |

```python
# UPSERT: on INSERT, omitted state defaults to 'new', omitted data to {}.
# On UPDATE, only the fields you pass change; the rest are preserved.
tf.collection('document').set('doc-123', state='loaded', data={'title': 'Q4 plan'})

# State-only transition — trigger the next stage without touching data:
tf.collection('document').set('doc-123', state='chunked')

# Data-only update — change the payload, keep the state:
tf.collection('document').set('doc-123', data={'title': 'Q4 plan v2'})

# INSERT a fresh row with an auto-UUID key:
new_key = tf.collection('chunk').add(state='new', data={'text': 'content'})
```

Writes return the key, never a boolean. **If a write fails it raises** — a
returned value always means success.

### The row shape

Every `on_state` handler receives the same row dict, and so does every read:

```python
{
    'factory_name': 'my_factory',
    'collection':   'document',
    'key':          'doc-123',
    'user_id':      'system',     # agent writes default to 'system'
    'data':         {...},        # the JSONB payload — always a dict
    'state':        'loaded',
    'created_at':   datetime(...),
    'updated_at':   datetime(...),
}
```

!!! note "`data`, not `value`"
    The Python surface always exposes the JSONB payload as `data`. (The
    underlying database column is named `value` for historical reasons; you'll
    only see that name if you query Postgres directly.) `data` is always a dict
    and defaults to `{}` when a row was written with no payload.

### How a write becomes a wake-up

Every write fires a change notification. The framework listens on a single
channel and treats a notification purely as an advisory "there may be work,
poll now" — it never carries the row body. You never deal with channels
directly: subscribing with `tf.on_state` wires this up for you, and the actual
work is always resolved by the poll query.

### Point reads

```python
row    = tf.collection('document').get('doc-123')    # full row dict, or None
exists = tf.collection('document').exists('doc-123')  # bool
```

A read returning `None` / `[]` always means **"not found"**, never "the query
failed" — failures raise. (`get` and `exists` are the exception: they
log-and-return on a connection error rather than raise, so a single bad lookup
can't abort an agent.)

---

## Querying

`tf.collection(name)` returns a **lazy query builder**. Filters chain and AND
together; nothing touches the database until you call a terminal.

### Filters

| Filter | Effect |
|---|---|
| `.state('X')` | `state = 'X'` |
| `.state(['X', 'Y'])` | `state IN ('X', 'Y')` |
| `.where("<dsl>")` | a payload/column predicate (grammar below); multiple `.where(...)` AND together |
| `.vector_search(q)` | order by similarity (see [Vector search](#embeddings-and-vector-search)) |
| `.limit(n)` | cap the result count |

!!! warning "Call `.state()` only once"
    Calling `.state(...)` twice raises `ValueError`. Combine the values into a
    single list: `.state(['ready', 'done'])`.

### Terminals

| Terminal | Returns |
|---|---|
| `.get_all()` / `.run()` (alias) | `list[row dict]`, ordered by `updated_at` descending |
| `.first()` | first row dict, or `None` |
| `.count()` | `int` |
| iterating (`for row in tf.collection(...).state(...)`) | yields rows |

```python
loaded   = tf.collection('document').state('loaded').get_all()
n_ready  = tf.collection('chunk').state('vectorised').count()
newest   = tf.collection('document').state('loaded').first()

ready_or_done = tf.collection('document').state(['ready', 'done']).get_all()
```

### The `.where()` string DSL

`.where("...")` is a small predicate language over a row's JSONB payload and a
whitelist of row columns. The string is **parsed and parameterized** — values
bind as SQL parameters, never concatenated into the query, so an f-string
predicate is safe against SQL injection.

**Operators:** `== != < > <= >= in "not in" and or not ( )`
**Literals:** string (single- or double-quoted), number, `true` / `false`,
list `[a, b, c]`

**Field namespaces** — three ways to name a field, because a payload key and a
lifecycle column can share a name:

| Reference | Resolves to | Example |
|---|---|---|
| bare field | JSONB payload key | `document == 'x'` |
| `data.field` | explicit payload alias (disambiguates) | `data.state == 'VIC'` |
| `meta.<col>` | a whitelisted row column | `meta.created_at > '2026-01-01'` |

The `meta.*` whitelist is `state`, `key`, `user_id`, `created_at`,
`updated_at`, `state_changed_at`. So bare `state` reads the **payload** field
`state`; `meta.state` reads the **lifecycle** column.

```python
# state filter + payload predicate + terminal
tf.collection('chunk').state('vectorised') \
    .where("document == 'ae400398.pdf' and token_count >= 400") \
    .get_all()

# OR and grouping are supported
tf.collection('lead') \
    .where("score >= 80 or (tier == 'gold' and active == true)") \
    .get_all()
```

!!! warning "DSL limitations to know"
    - **String literals have no escapes** — single- or double-quoted, but no
      backslash escapes, so a value containing an apostrophe or backslash can't
      be expressed in the DSL. Filter such cases in Python after a broader
      query.
    - **Rows missing the field are excluded.** Ordering comparisons
      (`< > <= >=`) cast the JSONB text to a number/bool with a guard, so a row
      whose value isn't a valid number/bool is dropped from the result rather
      than erroring the query. A row with the field entirely absent is likewise
      not matched.
    - `!=` compiles to `IS DISTINCT FROM`, so a NULL/absent field is correctly
      *not equal* to a value.
    - Not yet available: `.order_by(...)`, `.min_similarity(...)`,
      per-group top-N, and a query-level `.delete()`.

---

## LLMs

`tf.call_llm` is the single entry point for calling a language model. You give
it a prompt template, the inputs to fill it, and (usually) a Pydantic model
describing the structured output you want back.

```python
from pydantic import BaseModel, Field
from langchain_core.prompts import PromptTemplate

class Analysis(BaseModel):
    summary: str = Field(description="One-paragraph summary")
    score: float = Field(description="Confidence 0-1")

prompt = PromptTemplate.from_template(
    "Analyse this document:\n{text}\n{format_instructions}"
)

result = tf.call_llm(
    prompt,
    {"text": document_text},
    response_model=Analysis,
)
print(result.summary, result.score)   # result is an Analysis instance
```

When `response_model` is given, the framework appends format instructions to
your prompt, validates the model's reply against the schema, and returns a
typed instance. Omit `response_model` to get the cleaned response text back as
a plain string.

### Parameters

| Parameter | Default | Purpose |
|---|---|---|
| `prompt_template` | — | a LangChain `PromptTemplate` (or string template) |
| `prompt_inputs` | — | dict of values to fill the template |
| `response_model` | `None` | Pydantic class for structured output; `None` returns text |
| `provider` | env default | override `DEFAULT_LLM_PROVIDER` for this call |
| `model` | env default | override `DEFAULT_LLM_MODEL` for this call |
| `temperature` | provider default (≈0.3) | sampling temperature |
| `max_tokens` | `None` | cap on **output** tokens |

```python
# Route a cheap, high-volume call to a smaller model:
result = tf.call_llm(
    prompt,
    {"text": snippet},
    response_model=Analysis,
    provider='anthropic',
    model='claude-haiku-4-5-20251001',
    temperature=0.1,
)
```

!!! warning "`max_tokens` and long structured output"
    `max_tokens` caps the number of **output** tokens. It defaults to `None`,
    which passes nothing to the provider — so the provider's own default
    applies. Some clients cap output low by default (notably ChatAnthropic at
    1024 tokens), which can silently truncate a long structured response. If a
    reply needs room (e.g. an exhaustive list extracted from a long document),
    set a larger value such as `max_tokens=8192`. The framework maps it to each
    provider's native output-token argument for you.

The active provider and model come from the `DEFAULT_LLM_PROVIDER` and
`DEFAULT_LLM_MODEL` environment variables; the per-call `provider` / `model`
arguments override them for one call. Provider credentials are resolved
automatically — you do not pass API keys to `call_llm`.

---

## Embeddings and vector search

`tf.embed` turns text into a vector. Pass a single string for one vector, or a
list of strings for a batch.

```python
vector  = tf.embed("some text to embed")            # → list[float]
vectors = tf.embed(["text one", "text two"])         # → list[list[float]]

# per-call provider/model override
vector  = tf.embed("text", provider="openrouter", model="baai/bge-m3")
```

The provider and model come from `DEFAULT_EMBEDDING_PROVIDER` and
`DEFAULT_EMBEDDING_MODEL`.

!!! note "Supported dimensions"
    Vectors are stored in fixed-dimension columns: **256, 512, 768, 1024,
    1536, 3072**. The embedding model's output dimension must match one of
    these, or the write will fail.

### Storing a vector on a row

Pass `embedding=` to `.set()` or `.add()` and the vector is stored alongside
the row, ready for similarity search:

```python
text = "chunk of document text"
tf.collection('chunk').set(
    'chunk-1',
    state='vectorised',
    data={'text': text},
    embedding=tf.embed(text),
)
```

### Searching by similarity

`.vector_search(text_or_vec)` is a **chainable filter**, not a terminal — it
sets cosine-similarity ordering. You still call `.limit(n)` to cap the result
and a terminal (`.run()` / `.get_all()`) to execute. It composes with
`.state(...)` and `.where(...)` like any other filter.

```python
# pass a string — auto-embedded for you — then cap and run
hits = tf.collection('chunk').vector_search('budget overrun').limit(5).run()

# or pass a pre-computed vector
hits = tf.collection('chunk').vector_search(query_vec).limit(5).run()

# compose with state and payload filters
hits = tf.collection('chunk').state('vectorised') \
    .where("document == 'ae400398.pdf'") \
    .vector_search('budget overrun').limit(5).run()
```

Each hit is an ordinary row dict with one extra key, `similarity` (cosine,
roughly 0–1):

```python
for hit in hits:
    print(hit['similarity'], hit['data']['text'])
```

!!! note "Vector search is always bounded"
    `.vector_search()` with no `.limit()` defaults to a limit of 10, so an ANN
    search never accidentally scans the whole collection.

---

## Schedules

Run a job periodically with the fluent `tf.on_schedule` API:

```python
tf.on_schedule.every(10).seconds.do(top_up)
tf.on_schedule.every(5).minutes.do(refresh_cache)
tf.on_schedule.every().hour.do(hourly_job)
tf.on_schedule.every().day.at("10:30").do(daily_report)
tf.on_schedule.every().monday.do(weekly_job)
```

Scheduled jobs run inside the same `tf.run_pending()` tick as state dispatch,
so they require no extra wiring — just register them before the main loop.

!!! note "Aggregators belong on a schedule"
    Because a row is consumed by leaving its state, there's no "re-fire every
    tick" mechanism for pure summarizers. To maintain a running total or
    rollup, recompute it on a schedule and overwrite a **fixed-key** stats row:
    ```python
    @tf.on_schedule.every(1).minutes.do
    def recompute_totals():
        n = tf.collection('order').state('paid').count()
        tf.collection('stats').set('totals', data={'paid_orders': n})
    ```

---

## MCP tools

A factory can expose tools to the dashboard's chat assistant. Declare a server
once, then register one or more tools with a fluent builder:

```python
tf.add_mcp_server(
    name='spend-data',
    description='Query and analyse classified spend data',
)

def query_spend(params):
    # params is a dict matching the input schema below
    level = params['level']
    rows = tf.collection('spend').state('classified') \
        .where(f"level == '{level}'").get_all()
    return {"count": len(rows)}            # must be JSON-serializable

tf.add_mcp_tool('query_spend', 'Query spend totals by classification level') \
    .with_input({
        "type": "object",
        "properties": {
            "level": {
                "type": "string",
                "description": "Classification level",
                "enum": ["l1", "l2", "l3"],
            }
        },
        "required": ["level"],
    }) \
    .with_annotations({"readOnlyHint": True, "openWorldHint": False}) \
    .do(query_spend)
```

The order of `add_mcp_server()` and `add_mcp_tool()` doesn't matter; both are
published on the first `tf.run_pending()` tick. Each tool call arrives as a row
in a dedicated collection, and the framework routes it to your handler and
writes the result back automatically — you only write the handler.

### `.with_input(schema)`

A JSON Schema object describing the tool's parameters. The dict your handler
receives matches this schema.

### `.with_annotations(annotations)`

Optional, but **declare it on every externally exposed tool** — chat clients
use annotations to bucket tools, and an unannotated tool lands in a generic
"Other tools" group. Standard MCP keys:

| Key | Type | Meaning |
|---|---|---|
| `readOnlyHint` | bool | tool does not modify its environment |
| `destructiveHint` | bool | tool may perform destructive updates |
| `idempotentHint` | bool | repeated identical calls have no extra effect |
| `openWorldHint` | bool | tool touches external entities (set `false` for DB-only tools) |
| `title` | str | human-readable display title |

```python
# read-only query tool
.with_annotations({"readOnlyHint": True, "openWorldHint": False})

# write tool (non-destructive insert, not idempotent)
.with_annotations({"readOnlyHint": False, "destructiveHint": False,
                   "idempotentHint": False, "openWorldHint": False})
```

The handler's return value must be JSON-serializable (dict, list, str, number,
bool, or `None`). Raising inside a handler is reported back to the caller as a
tool error.

---

## Bucket store (file volumes)

`tf.bucket_store(name)` is the agent-facing API for a factory **volume** — a
named file space defined in `factory.yml`. Use it instead of `open()` /
`os.scandir` against `/app/volumes/...`; the bucket store works the same way
regardless of where the files actually live.

```python
pdfs = tf.bucket_store('agreements')

for path in pdfs.list():            # ['ae400398.pdf', 'sub/dir.pdf', ...]
    data = pdfs.read(path)          # bytes
    # ... process the bytes ...

pdfs.write('summary.txt', b'...')   # str is utf-8 encoded for you
if pdfs.exists('report.pdf'):
    pdfs.delete('stale.pdf')

with pdfs.open('huge.pdf') as f:    # streaming handle for large objects
    head = f.read(4096)
```

### Methods

| Method | Returns | Notes |
|---|---|---|
| `list(prefix='')` | `list[str]` | object paths relative to the volume root, forward-slash separated; missing prefix → `[]` |
| `read(path)` | `bytes` | whole-object read; raises `BucketNotFoundError` if absent |
| `open(path)` | binary stream | context-managed file-like object; for large objects |
| `write(path, data)` | `None` | `data` is `bytes` or `str` (utf-8); raises if the attachment is read-only |
| `delete(path)` | `None` | removes the object |
| `exists(path)` | `bool` | |
| `url(path)` | `str` | a reference to the object (not always browser-openable) |

### Attachment model

An agent reaches only the volumes it explicitly attaches in `factory.yml`,
with a per-attachment `read` or `write` mode. Attaching a volume `read` makes
writes fail; not attaching a volume at all makes every operation fail.

### Errors are raised, not swallowed

!!! warning "The bucket store surfaces failures"
    Unlike some convenience layers that fall back silently, **file operations
    raise on failure** — silently returning empty bytes or pretending a write
    succeeded could make an agent process the *wrong* data, which is worse than
    a loud failure.

| Condition | Exception |
|---|---|
| no such file/prefix | `BucketNotFoundError` |
| operation denied (unattached volume, or write to a read-only attachment) | `BucketPermissionError` |
| payload over the size cap | `BucketConflictError` |
| bad path / other I/O / network / timeout | `BucketStoreError` |

All four derive from `BucketStoreError`, so `except tf.BucketStoreError`
catches the whole family; catch a specific subclass when you want to branch
(e.g. treat a missing file as skippable but a permission error as fatal).

---

## Utilities

### Logging

Five logging functions, all with the signature `(message: str)`:

```python
tf.log_debug("Detailed debug info")
tf.log_info("General status message")
tf.log_warn("Warning condition")
tf.log_error("Error occurred")
tf.log_persona("First-person message for the UI's chat-bubble view")
```

Every call writes to stdout and, when running under the orchestrator, to the
log store visible in the dashboard. `log_persona` is rendered as a speech
bubble; otherwise it behaves like `log_info`.

!!! warning "Don't log secrets"
    Log lines are persisted and visible to anyone with log-read access. Never
    include API keys or other secrets in a log message.

### Time and IDs

```python
tf.get_timestamp()      # local-time ISO-8601 string, honouring $TZ (defaults UTC)
tf.get_timestamp_utc()  # UTC ISO-8601 string
tf.generate_unique_id() # UUID hex string
```

Both timestamp helpers return timezone-aware ISO-8601 strings.

### Secrets

```python
api_key = tf.secrets('SOME_API_KEY')               # → str | None
api_key = tf.secrets('SOME_API_KEY', default='')   # never returns None
```

`tf.secrets()` is a **read-only** lookup against the orchestrator's secrets
store, falling back transparently to the environment variable of the same name
when the key isn't in the store. It **never raises** — agents keep running even
if the store is briefly unreachable, falling back to the environment in that
case. There is no `set` / `rotate` / `list`; writes happen through the
dashboard. (LLM and embedding provider credentials are resolved this way for
you automatically — you don't pass keys into `tf.call_llm` or `tf.embed`.)

### Lifecycle

```python
tf.sleep(5)             # shutdown-aware sleep; use instead of time.sleep
tf.shutting_down()      # bool — True once SIGTERM/SIGINT has been received
```

The main loop never *needs* `tf.shutting_down()` — `tf.run_pending()` and
`tf.sleep()` exit the process on their own once a shutdown signal arrives. It's
there for long-running handlers that want to bail out early at a safe
checkpoint:

```python
@tf.on_state('document', 'loaded').do
def handle(item):
    for chunk in big_iter(item):
        if tf.shutting_down():
            return                 # row stays in 'loaded'; retried on restart
        process(chunk)
    tf.collection('document').set(item['key'], state='processed')
```

### Configuration

A handful of read-only identity values come from the environment:

```python
tf.FACTORY_NAME   # this factory's name
tf.AGENT_NAME     # this agent's display name (editable in the dashboard)
tf.AGENT_SLUG     # canonical agent identifier; stable across renames
tf.AGENT_ID       # per-container/pod identifier; distinguishes replicas
```

`FACTORY_NAME` and `AGENT_NAME` are the two you'll use most often (e.g. in log
messages or routing). Behavioural settings like the LLM provider/model are
read by the framework from environment variables — you don't read them
yourself.

---

## Public API surface

Everything exported from `import teenyfactories as tf`:

| Group | Names |
|---|---|
| Logging | `log_debug`, `log_info`, `log_warn`, `log_error`, `log_persona` |
| Time / IDs | `get_timestamp`, `get_timestamp_utc`, `generate_unique_id` |
| LLM | `call_llm` |
| Embeddings | `embed` |
| Secrets | `secrets` |
| Data | `collection` |
| Pub/sub | `on_state`, `run_pending` |
| Scheduling | `on_schedule` |
| MCP | `add_mcp_server`, `add_mcp_tool` |
| Bucket store | `bucket_store`, `BucketStoreError`, `BucketNotFoundError`, `BucketPermissionError`, `BucketConflictError` |
| Lifecycle | `sleep`, `shutting_down` |
| Config | `FACTORY_NAME`, `AGENT_NAME`, `AGENT_SLUG`, `AGENT_ID` |

If a name isn't in this list, it isn't part of the supported surface — don't
reach into submodules.

---

## Patterns

### A pipeline stage

The bread-and-butter agent: react to a state, do work, transition the row.

```python
"""
Agent: Summariser

Purpose: Summarise loaded documents with an LLM.
Triggers: document rows entering state 'loaded'
Outputs: document → 'summarised'
"""
import teenyfactories as tf
from pydantic import BaseModel, Field
from langchain_core.prompts import PromptTemplate


class Summary(BaseModel):
    summary: str = Field(description="One-paragraph summary")


PROMPT = PromptTemplate.from_template(
    "Summarise this document:\n{text}\n{format_instructions}"
)


@tf.on_state('document', 'loaded').do
def summarise(item):
    tf.log_persona(f"Summarising {item['key']}...")
    result = tf.call_llm(PROMPT, {"text": item['data']['text']},
                         response_model=Summary)
    tf.collection('document').set(
        item['key'],
        state='summarised',
        data={**item['data'], 'summary': result.summary},
    )


tf.log_info("Summariser started")

while True:
    tf.run_pending()
    tf.sleep(1)
```

### A scheduled top-up

An agent that periodically replenishes a queue rather than reacting to events.

```python
"""
Agent: Lead Topper

Purpose: Keep ~50 leads queued for outreach.
Triggers: a schedule (every 5 minutes)
Outputs: lead rows at 'queued'
"""
import teenyfactories as tf


@tf.on_schedule.every(5).minutes.do
def top_up():
    queued = tf.collection('lead').state('queued').count()
    shortfall = 50 - queued
    if shortfall <= 0:
        return
    for lead in fetch_new_leads(shortfall):     # your own source
        tf.collection('lead').add(state='queued', data=lead)
    tf.log_info(f"Topped up {shortfall} leads")


tf.log_info("Lead Topper started")

while True:
    tf.run_pending()
    tf.sleep(1)
```

### An MCP tool

Expose a read-only query to the dashboard chat assistant.

```python
"""
Agent: Spend Query Tool

Purpose: Let the chat assistant query classified spend.
Triggers: MCP tool calls; (no state subscriptions)
Outputs: tool results
"""
import teenyfactories as tf

tf.add_mcp_server(name='spend-data',
                  description='Query classified spend data')


def query_spend(params):
    level = params['level']
    n = tf.collection('spend').state('classified') \
        .where(f"level == '{level}'").count()
    return {"level": level, "count": n}


tf.add_mcp_tool('query_spend', 'Count classified spend rows at a level') \
    .with_input({
        "type": "object",
        "properties": {
            "level": {"type": "string", "enum": ["l1", "l2", "l3"]},
        },
        "required": ["level"],
    }) \
    .with_annotations({"readOnlyHint": True, "openWorldHint": False}) \
    .do(query_spend)


tf.log_info("Spend Query Tool started")

while True:
    tf.run_pending()
    tf.sleep(1)
```
