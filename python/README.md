# teenyfactories

Agent runtime for [TeenyFactories](https://github.com/teenyfactories/core). Postgres-backed pub/sub, multi-provider LLM calls, vector search.

```bash
pip install --pre teenyfactories
```

```python
import teenyfactories as tf

@tf.on_state('documents', 'loaded').do
def analyse(item):
    tf.collection('documents').set(item['key'], state='analysed')

while True:
    tf.run_pending()
    tf.sleep(1)
```

Writing a row into a `(collection, state)` makes every subscriber to that pair pick it up: `(collection, state)` is a FIFO queue, and the handler consumes a row by transitioning its state (or deleting it). That's the framework.

## Public API

| Group | Names |
|---|---|
| Pub/sub | `on_state`, `run_pending` (the only pub/sub primitive — no message bus) |
| Data | `collection` (`.set`, `.add`, `.get`, `.get_all`, `.remove`, `.count`, `.exists`, `.first`, `.state`, `.where`, `.vector_search`), `embed` |
| LLM | `llm` (fluent builder — `.ask` / `.run_agent_loop`), `call_llm` (LEGACY) |
| MCP | `add_mcp_server`, `add_mcp_tool` |
| Schedule | `on_schedule.every(N).<unit>.do(handler)` |
| Secrets / files | `secrets`, `bucket_store` (+ `BucketStoreError` family) |
| Logging | `log_debug`, `log_info`, `log_warn`, `log_error`, `log_persona` |
| Time / IDs | `get_timestamp`, `get_timestamp_utc`, `generate_unique_id` |
| Debug | `breakpoint` |
| Lifecycle / config | `sleep`, `shutting_down`, `FACTORY_NAME`, `AGENT_NAME`, `AGENT_SLUG`, `AGENT_ID`, `__version__` |

Anything not in this list is not part of the supported surface — don't reach into submodules.

## How it plugs in

A factory is a directory:

```
my_factory/
├── factory.yml
└── agents/
    ├── poller.py
    └── enricher.py
```

Each `agents/*.py` runs in its own container based on `ghcr.io/teenyfactories/agent:dev` — which has this library pre-installed. Single-factory mode runs each file as a `docker compose` service; multi-factory mode runs them under the orchestrator. Either way, agent code is just `import teenyfactories as tf` + handlers + main loop.

Full setup, compose templates, environment variable reference: see the [core repo](https://github.com/teenyfactories/core).

## Versioning

PEP 440 dev pre-releases: `0.1.0.devYYYYMMDD`. `pip install teenyfactories` will NOT pick these up — you need `--pre` (latest dated build) or an explicit pin (`teenyfactories==0.1.0.dev20260512`).

## License

MIT.
