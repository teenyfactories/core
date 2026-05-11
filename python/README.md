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

Writing a row with a given state fires `NOTIFY {factory}.{collection}.{state}`. Every subscriber to that pair gets the row. That's the framework.

## Public API

| Group | Names |
|---|---|
| Pub/sub | `on_state`, `on_message`, `send_message`, `run_pending` |
| Data | `collection` (`.set`, `.add`, `.get`, `.get_all`, `.remove`, `.count`, `.exists`, `.vector_search`) |
| LLM | `call_llm`, `embed` |
| MCP | `add_mcp_server`, `add_mcp_tool` |
| Schedule | `on_schedule.every(N).<unit>.do(handler)` |
| Logging | `log_debug`, `log_info`, `log_warn`, `log_error`, `log_persona` |
| Time / IDs | `get_timestamp`, `get_timestamp_utc`, `generate_unique_id` |
| Util | `sleep`, `PROJECT_NAME`, `FACTORY_PREFIX`, `__version__` |

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
