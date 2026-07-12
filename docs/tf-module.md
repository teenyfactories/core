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
Some call an LLM (`tf.llm()`), some only move data around — that's a
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

`tf.llm()` is the entry point for calling a language model — a **config-first
fluent builder** with **eager** terminals. You build up the call (model, system
prompt, tuning, optional structured output) with chainable config links, then
end with a terminal that fires the call and returns the result. The builder
holds no result; the work happens when you call a terminal.

```python
import teenyfactories as tf

# Plain text in, text out.
answer = tf.llm().ask("Summarise this in one line: {text}", {"text": data})

# With a system prompt + tuning.
answer = (
    tf.llm()
    .system("You are a terse analyst.")
    .temperature(0.1)
    .max_tokens(8192)
    .ask("Analyse: {text}", {"text": data})
)
```

`.ask(prompt, inputs)` takes a prompt **string or `PromptTemplate`** and an
`inputs` dict; `{placeholders}` in the prompt are filled from `inputs`. With
`.system(text)`, braces in the system text are **not** parsed as template
variables (it's a literal system message) — only the human prompt is templated.

### Config links

| Link | Effect |
|---|---|
| `.model(name)` | Override `DEFAULT_LLM_MODEL` for this call |
| `.provider(name)` | Override `DEFAULT_LLM_PROVIDER` (`openai` / `anthropic` / `google` / `ollama` / `azure_bedrock` / `digitalocean` / `openrouter`) |
| `.temperature(t)` | Sampling temperature (provider default ≈0.3) |
| `.max_tokens(n)` | Cap on **output** tokens |
| `.system(text)` | System prompt (literal; not templated) |
| `.with_structured_output(Model)` | Return a parsed Pydantic instance instead of text (see below) |

```python
# Route a cheap, high-volume call to a smaller model:
answer = (
    tf.llm()
    .provider('anthropic')
    .model('claude-haiku-4-5-20251001')   # overrides DEFAULT_LLM_MODEL
    .temperature(0.1)
    .ask("Classify: {text}", {"text": snippet})
)
```

The active provider and model come from the `DEFAULT_LLM_PROVIDER` and
`DEFAULT_LLM_MODEL` environment variables; `.provider(...)` / `.model(...)`
override them for one call. Provider credentials are resolved automatically —
you never pass API keys to `tf.llm()`.

!!! warning "`max_tokens` and long structured output"
    `.max_tokens(n)` caps the number of **output** tokens. Omit it and nothing
    is passed to the provider, so the provider's own default applies. Some
    clients cap output low by default (notably **ChatAnthropic at 1024
    tokens**), which can silently truncate a long structured response. If a
    reply needs room (e.g. an exhaustive list extracted from a long document),
    set a larger value such as `.max_tokens(8192)`. The framework maps it to
    each provider's native output-token argument for you (langchain
    `max_tokens` for OpenAI / Anthropic / DigitalOcean / OpenRouter, Google
    `max_output_tokens`, Ollama `num_predict`, Azure o3
    `max_completion_tokens`).

!!! note "Temperature is silently dropped for reasoning-class models"
    Anthropic's Opus 4.7+ family (and the same models proxied via DigitalOcean)
    reject the `temperature` request kwarg. The framework detects these by
    model-ID substring and omits it, so `.temperature(...)` is harmless but has
    no effect on those models.

### Terminals (eager)

A terminal fires the call. There are two single-shot terminals and two agentic
ones; each has a `_with_meta` variant that also returns a telemetry dict.

| Terminal | Returns | When |
|---|---|---|
| `.ask(prompt, inputs)` | `output` (str, or the structured `Model`) | single-shot |
| `.ask_with_meta(prompt, inputs)` | `(output, meta)` | single-shot + telemetry |
| `.run_agent_loop(task)` | `output` (str) | agentic tool-calling loop |
| `.run_agent_loop_with_meta(task)` | `(output, meta)` | loop + telemetry |

### Structured output

`.with_structured_output(Model)` returns a parsed Pydantic instance instead of
text. It **prefers the provider's native enforcement** (LangChain
`with_structured_output`) and **falls back** to a `PydanticOutputParser` path
(inject format instructions → invoke → clean → parse) when a provider/model
doesn't enforce the schema. The degrade is logged at **debug** level (so an
OpenRouter soft-failure — accepts the parameter but the upstream ignores it —
is visible without noise). On OpenRouter the call additionally routes with
`require_parameters` so only upstreams that honour structured output are
selected.

```python
from pydantic import BaseModel, Field

class Analysis(BaseModel):
    summary: str = Field(description="One-paragraph summary")
    score: float = Field(description="Confidence 0-1")

result = (
    tf.llm()
    .with_structured_output(Analysis)
    .ask("Analyse this document:\n{text}", {"text": document_text})
)
print(result.summary, result.score)   # result is an Analysis instance
```

### The `meta` dict

The `_with_meta` terminals (and `tf.embed(...).with_meta()`) return a plain
dict — a read-view over the verbatim provider telemetry. **Every field is
best-effort and may be `None`.**

```python
output, meta = tf.llm().ask_with_meta("Analyse: {text}", {"text": data})

meta = {
    "provider":      "anthropic",   # resolved provider
    "model":         "claude-...",  # provider's reported model, else the requested one
    "cost":          0.0021,        # USD — OpenRouter-only; None elsewhere
    "finish_reason": "stop",        # verbatim provider value (None if unreported)
    "latency_ms":    842,
    "usage":         {...},         # verbatim LangChain usage_metadata
    "raw":           {...},         # verbatim usage + response-metadata blob
}
```

- `usage` is the verbatim LangChain `usage_metadata` (`input_tokens` /
  `output_tokens` / `total_tokens` / `input_token_details.{cache_read,
  cache_creation}` / …). Shapes are **provider-dependent** — treat a missing
  key as "not reported", not zero.
- `cost` is populated only when the provider reports it (OpenRouter). tf
  computes **no** cost itself; the orchestrator prices off `raw` at read time.
- `finish_reason` is the provider's **verbatim** value — there is **no custom
  outcome enum** in core. A factory that wants its own labels (e.g.
  `finished` / `maxed` / `gave_up`) maps them in factory code.

!!! note "Usage logging is automatic"
    Every `tf.llm()` / `tf.embed()` call is logged transparently to factory
    usage tracking — you don't call a helper. Cost is **not** computed
    tf-side; usage is stored verbatim and the orchestrator computes USD at
    read time. Logging failures never break the underlying call. (A
    cost-based spend gate also runs automatically before each call; it
    **fails open**, so an orchestrator hiccup never blocks real work.)

### Agentic loop (`.run_agent_loop*`)

`.run_agent_loop(task)` runs a **native provider tool-calling** ReAct loop: the
model is bound to a set of tools, calls them, sees the results, and continues
until it stops calling tools (or hits `max_turns`). The loop is **eager** —
tools mutate state as it runs.

```python
out, meta = (
    tf.llm()
    .system(SYSTEM_PROMPT)
    .add_tools_from_self()        # bind THIS agent's own tf.add_mcp_tool handlers
    .max_turns(50)
    .run_agent_loop_with_meta(task_text)
)
```

**Tool sourcing — same factory only.** Cross-**factory** tool binding is not
available (cut/parked — security + DB gated).

| Link | Binds | Dispatch |
|---|---|---|
| `.add_tools_from_self()` | this agent's own `tf.add_mcp_tool` handlers | local, in-process |
| `.add_tools_from_agent(name)` | another agent's published tools **in this factory** | over the wire (request/response rows) |
| `.add_tool(fn_or_name)` | a single callable, or a named local MCP tool | local |

**Loop controls:**

| Link | Effect |
|---|---|
| `.max_turns(n)` | Hard cap on turns — **the sole runaway guard**. There is no repeat/stuck detector; a uselessly-looping model runs until `max_turns`, then stops with `max_turns_reached=True` |
| `.on_turn(cb)` | Called after each turn with `{turn, tool_calls, usage}` — for live progress. Exceptions in the callback are logged and swallowed |
| `.inject_tool_args(mapping, tools=None)` | Force these args into tool calls **at dispatch time**, hidden from the model's `inputSchema`. `tools=` limits which tools get the injection (`None` = all). Used for forced provenance (e.g. stamping a `source` onto every mutating tool) |

The `_with_meta` loop terminal's `meta` carries the same single-shot fields
**plus** loop-level signals:

```python
meta["turns"]              # per-turn dicts: {turn, tool_calls (names), usage}
meta["tool_calls"]         # flat list across the run: [{name, args, result}, ...]
meta["max_turns_reached"]  # bool — the ONLY loop-level signal with no provider analog
meta["usage"]              # folded across all turns (tokens + cache counts summed)
```

`max_turns_reached` is the single runaway signal; `finish_reason` still
reflects the last turn's provider value.

!!! note "Provider support for the agentic loop"
    `.run_agent_loop*` requires native `bind_tools`: **openai**,
    **openrouter**, **digitalocean** (all `ChatOpenAI`), **anthropic**, and
    **google**. **Ollama** is partial. **Azure o3** is unsupported — the loop
    raises a `NotImplementedError` capability error telling you to use `.ask*`
    or switch provider. `.ask*` and `tf.embed` work on **all** providers.

**Reliability layer (built in, no config):**

- **Serial multi-tool dispatch** — when a turn returns several tool calls they
  run one at a time, in order. No threads (matches tf's single-threaded
  `run_pending` model).
- **Arg validation fed back to the model** — a tool call with missing/wrong
  args isn't dispatched; the validation error is returned as the tool result so
  the model can correct itself.
- **Tool errors fed back** — a handler that raises (or a wire timeout) returns
  an `{"error": ...}` result rather than crashing the loop.
- **Bounded history + per-result cap** — the message window is trimmed (system
  prefix kept) and each tool result is capped; a truncated result carries an
  explicit `[TRUNCATED — ... PARTIAL ...]` completeness marker so the model
  knows it isn't the full output.
- **API retry/backoff** — transient provider errors (429 / 5xx / overloaded /
  timeout) are retried with exponential backoff, respecting
  `tf.shutting_down()` so SIGTERM doesn't hang.

!!! note "Prompt caching is always on"
    There is no opt-in. The cacheable system+tools prefix is what makes it
    effective in the loop. Anthropic-direct marks two breakpoints at the message
    layer: a static one on the system block and a rolling one on the last message
    each turn (`cache_control: ephemeral`), so the growing tool-loop prefix caches
    turn to turn. OpenAI/OpenRouter prefix caching is automatic per upstream; the
    loop reuses a single client, so a caller's stable `extra_body` (e.g.
    `provider.order`) rides every turn — best-effort consistent routing. tf does
    not pin or auto-select an OpenRouter upstream (langchain-openai does not expose
    the served upstream, so it can't be read back); a factory that needs hard
    stickiness sets its own `provider.order`.

!!! note "`tf.call_llm` is legacy"
    `tf.call_llm(prompt, inputs, response_model=...)` is the **legacy**
    single-shot API — still exported and byte-for-byte unchanged (it *is* the
    `PydanticOutputParser` path that `tf.llm()` reuses as its structured-output
    fallback), and it logs a debug deprecation breadcrumb. Migrate to
    `tf.llm().ask(...)` (text) / `tf.llm().with_structured_output(M).ask(...)`
    (structured). Note `call_llm` takes `provider=` (not `model_provider=`).

---

## Embeddings and vector search

`tf.embed(text)` turns text into a vector. It is **input-first and lazy**: the
result **is** the vector — a `list` subclass that computes on first
value-access (then memoises). Configure it with `.provider(...)` /
`.model(...)` before use; the laziness is invisible — index it, iterate it, or
pass it as `embedding=` and it resolves on demand. Pass a single string for one
vector, or a list of strings for a batch.

```python
vector  = tf.embed("some text to embed")            # → vector (list of floats)
vectors = tf.embed(["text one", "text two"])         # → list of vectors

# fluent per-call provider/model override
vector  = tf.embed("text").provider("openrouter").model("baai/bge-m3")

# with telemetry — .with_meta() resolves eagerly and returns (vector, meta)
vector, meta = tf.embed("query").model("text-embedding-3-large").with_meta()
```

`.with_meta()` returns `(vector, meta)` where `meta` has the same shape as the
LLM [`meta` dict](#the-meta-dict) (`finish_reason` is always `None` for
embeddings; `cost` is OpenRouter-only).

The provider and model come from `DEFAULT_EMBEDDING_PROVIDER` and
`DEFAULT_EMBEDDING_MODEL`.

!!! note "`tf.embed(text, provider=, model=)` is legacy"
    The old eager-kwargs form `tf.embed(text, provider=p, model=m)` still works
    (it pre-configures the builder) and logs a debug deprecation breadcrumb.
    Migrate to `tf.embed(text).provider(p).model(m)`.

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
you automatically — you don't pass keys into `tf.llm()` or `tf.embed`.)

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
| LLM | `llm`, `call_llm` (legacy) |
| Embeddings | `embed` |
| Secrets | `secrets` |
| Data | `collection` |
| Pub/sub | `on_state`, `run_pending` |
| Scheduling | `on_schedule` |
| MCP | `add_mcp_server`, `add_mcp_tool` |
| Bucket store | `bucket_store`, `BucketStoreError`, `BucketNotFoundError`, `BucketPermissionError`, `BucketConflictError` |
| Lifecycle | `sleep`, `shutting_down` |
| Stepped debugging | `breakpoint` |
| Config | `FACTORY_NAME`, `AGENT_NAME`, `AGENT_SLUG`, `AGENT_ID` |
| Version | `__version__` |

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


class Summary(BaseModel):
    summary: str = Field(description="One-paragraph summary")


@tf.on_state('document', 'loaded').do
def summarise(item):
    tf.log_persona(f"Summarising {item['key']}...")
    result = (
        tf.llm()
        .with_structured_output(Summary)
        .ask("Summarise this document:\n{text}", {"text": item['data']['text']})
    )
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
