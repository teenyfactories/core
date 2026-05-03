# TeenyFactories

**Python framework for distributed agent systems backed by Postgres.**

`teenyfactories` (imported as `tf`) is the framework that every TeenyFactories agent runs on top of. It gives you:

- A multi-provider LLM call (`tf.call_llm`) with automatic Pydantic parsing and per-call usage logging.
- A Postgres-backed pub/sub primitive (`tf.on_state`, `tf.on_message`, `tf.send_message`) over LISTEN/NOTIFY.
- A typed key-value-and-state store (`tf.collection`) that drives the lifecycle.
- Vector embeddings + ANN search (`tf.embed`, `tf.collection(...).vector_search(...)`).
- MCP tool registration so factory agents can expose tools to the orchestrator's chat LLM.
- Scheduling, structured logging, timestamps, IDs, and a single-call event loop (`tf.run_pending`).

Everything routes through `factory_data` in Postgres; there is no Redis, no message broker, no separate state file.

## Installation

For local development inside the TeenyFactories monorepo:

```bash
cd core
pip install -e "python/[dev]"
```

Inside containers, the framework is pre-installed in the base image `ghcr.io/teenyfactories/agent:dev` (built from `core/python/Dockerfile.build`). Agent scripts mount at `/app/script.py` and `import teenyfactories as tf` works out of the box — no `sys.path` mangling.

## Quick Start

```python
import teenyfactories as tf
from pydantic import BaseModel, Field
from langchain_core.prompts import PromptTemplate


class Insight(BaseModel):
    summary: str = Field(description="One-sentence summary")
    score: float = Field(description="Confidence 0..1")


# React when a row in `documents` enters state 'loaded'.
@tf.on_state('documents', 'loaded').do
def analyse(item):
    tf.log_info(f"Analysing {item['key']}")

    prompt = PromptTemplate.from_template(
        "Summarise: {text}\n{format_instructions}"
    )
    result = tf.call_llm(
        prompt,
        {"text": item['data']['content']},
        response_model=Insight,
    )

    # Persist the analysis as a new row, and advance the source row.
    tf.collection('insights').add(
        state='ready',
        data={'document_key': item['key'], **result.model_dump()},
    )
    tf.collection('documents').set(item['key'], state='analysed')


tf.log_info("analyser starting")

while True:
    tf.run_pending()
    tf.sleep(1)
```

## Public API at a glance

| Group | Names |
|---|---|
| Logging | `log_debug`, `log_info`, `log_warn`, `log_error`, `log_persona` |
| Time / IDs | `get_timestamp`, `get_timestamp_utc`, `generate_unique_id` |
| LLM | `call_llm` |
| Pub/sub | `send_message`, `on_message`, `on_state`, `run_pending` |
| MCP | `add_mcp_server`, `add_mcp_tool` |
| Data | `collection`, `embed` |
| Scheduling | `on_schedule`, `sleep` |
| Config | `PROJECT_NAME`, `FACTORY_PREFIX` |
| Versioning | `__version__` |

If a symbol is not in this list, it is not part of the supported surface — don't reach into submodules.

## Pub/sub

All factory data lives in `factory_data`. Every row carries a `state` column; INSERTs and state-changing UPDATEs fire `NOTIFY {factory}.{collection}.{state}`.

```python
# Subscribe to lifecycle (primary primitive)
@tf.on_state('documents', 'loaded').do
def handle(item):
    ...

# Subscribe to a fire-and-forget topic (sugar over on_state('_messages', 'topic'))
@tf.on_message('rebuild_index').do
def rebuild(item):
    ...

# Emit a fire-and-forget message
tf.send_message('rebuild_index').with_data({'reason': 'manual'})
```

Handlers always receive the full row dict:

```python
{
    'factory_name': 'my_factory',
    'collection':   'documents',
    'key':          'abc123',
    'user_id':      'system',
    'data':         {...},        # JSONB payload (DB column is `value`, surfaced as `data`)
    'state':        'loaded',
    'created_at':   datetime(...),
    'updated_at':   datetime(...),
}
```

### Replay semantics

By default, `on_state` listens for new NOTIFY events only — it does NOT replay rows already sitting in `(collection, state)` at startup. Opt in when you need durable resume-on-restart:

```python
tf.on_state('documents', 'loaded').on_startup_replay_latest().do(handle)

# Replay only the most recent row at startup (e.g. for "current config" patterns):
tf.on_state('config', 'current') \
    .on_startup_replay_latest() \
    .process_latest_only() \
    .do(reload_config)
```

## Data collections

`tf.collection(name)` is the only way to read or write `factory_data`. Every method returns a key, a row dict, a list of row dicts, an int, or a bool — never a tuple, never a raw cursor.

```python
# UPSERT (state-only, data-only, or both)
tf.collection('documents').set('doc-1', state='loaded', data={'title': 't'})
tf.collection('documents').set('doc-1', state='chunked')          # state-only transition
tf.collection('documents').set('doc-1', data={'title': 'new'})    # data-only

# INSERT new (auto-UUID key); state required
new_key = tf.collection('chunks').add(state='new', data={'text': '...'})

# Reads
row     = tf.collection('documents').get('doc-1')             # dict | None
loaded  = tf.collection('documents').get_all(state='loaded')  # list[dict]
n       = tf.collection('documents').count(state='loaded')    # int
exists  = tf.collection('documents').exists('doc-1')          # bool

# Delete one (cascades to factory_vectors). No bulk-delete by design.
tf.collection('chunks').remove('chunk-xyz')

# Vector search — pass a string (auto-embedded) or a vector
hits = tf.collection('chunks').vector_search('budget overrun', limit=5)
hits = tf.collection('chunks').vector_search(query_vec, limit=5, state='ready')
```

`get_all` returns rows ordered by `updated_at` descending. Vector search adds an extra `similarity` key (cosine, 0..1).

### Embeddings

```python
vector = tf.embed("text to embed")
vectors = tf.embed(["batch", "of", "texts"])

tf.collection('chunks').set(
    'chunk-1',
    state='ready',
    data={'text': '...'},
    embedding=vector,    # routed to factory_vectors / dim column
)
```

Supported dims: 256, 512, 768, 1024, 1536, 3072. Provider via `DEFAULT_EMBEDDING_PROVIDER` / `DEFAULT_EMBEDDING_MODEL`.

## LLM calls

```python
from pydantic import BaseModel, Field
from langchain_core.prompts import PromptTemplate

class Result(BaseModel):
    answer: str = Field(description="The answer")
    score: float = Field(description="Confidence 0..1")

prompt = PromptTemplate.from_template("Q: {q}\n{format_instructions}")

result = tf.call_llm(
    prompt,
    {"q": "what is the meaning of life?"},
    response_model=Result,
    temperature=0.3,                              # optional
    model='claude-haiku-4-5-20251001',            # optional per-call override
    model_provider='anthropic',                   # optional per-call override
)
```

Every call is automatically logged to `factory_logs` (provider, model, latency, tokens, prompt preview). Failures of the logging path never break the underlying call.

## Scheduling

```python
tf.on_schedule.every(10).minutes.do(periodic_job)
tf.on_schedule.every().hour.do(periodic_job)
tf.on_schedule.every().day.at("10:30").do(periodic_job)
tf.on_schedule.every().monday.do(periodic_job)
```

## Logging

All five loggers share the same signature `(message: str)` — no `level=`, no `metadata=`.

```python
tf.log_debug("…")
tf.log_info("…")
tf.log_warn("…")
tf.log_error("…")
tf.log_persona("first-person line for the chat UI bubbles")
```

## Time

```python
tf.get_timestamp()      # local-time ISO-8601 honouring $TZ (defaults UTC); always tz-aware
tf.get_timestamp_utc()  # UTC ISO-8601; always tz-aware
```

The orchestrator forwards `TZ` to every agent container so timestamps match the deployment locale without per-factory config.

## Main loop

Every agent script ends with:

```python
while True:
    tf.run_pending()
    tf.sleep(1)
```

`tf.run_pending()` takes no arguments. The first call also bootstraps the lifecycle (opens the LISTEN connection, publishes the MCP catalog).

## Error handling

- Reads return `None` / `[]` only for genuine "not found" — never to hide a DB error. Failures raise.
- Writes return the row key. Failures raise. No booleans.
- Inside handlers, catch known/expected conditions and transition the row to a terminal state; let unexpected exceptions propagate so `run_pending`'s per-handler catch records them.

## Environment

| Variable | Purpose |
|---|---|
| `FACTORY_PREFIX` | Factory name; injected by the orchestrator. Used in NOTIFY channel prefix. |
| `AGENT_NAME` | Agent display name. |
| `TZ` | POSIX timezone for `tf.get_timestamp()`. Defaults to UTC. |
| `DEFAULT_LLM_PROVIDER` | `openai`, `anthropic`, `google`, `ollama`, `azure_bedrock` |
| `DEFAULT_LLM_MODEL` | Model name. Overridable per-call via `tf.call_llm(..., model='...')`. |
| `DEFAULT_EMBEDDING_PROVIDER` | `openai`, `ollama` |
| `DEFAULT_EMBEDDING_MODEL` | e.g. `text-embedding-3-small` |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY` | Provider keys |

Postgres connection env vars are internal and read directly inside the core; agent code does not touch them.

## Tests

```bash
pip install -e "python/[dev]"
pytest
```

## License

MIT — see `LICENSE`.
