# TeenyFactories

**Open-core framework for distributed AI agent factories.**

```python
import teenyfactories as tf

@tf.on_state('documents', 'loaded').do
def analyse(item):
    tf.collection('documents').set(item['key'], state='analysed')

while True:
    tf.run_pending()
    tf.sleep(1)
```

## What's here

| | |
|---|---|
| [`python/`](python/) | The `teenyfactories` library. **MIT open source.** `pip install teenyfactories`. Full library docs in [`python/README.md`](python/README.md) — also the PyPI page. |
| `ghcr.io/teenyfactories/agent:dev` | Container base image with the library pre-installed. Every factory agent runs on this. Built from `python/Dockerfile.build`. |

## What sits around it

- **Orchestrator** — proprietary app that discovers factories, spawns agent containers, and provides the UI / chat / state-graph editor. Separate repo.
- **Factories** — your code. One per problem domain. `factory.yml` + `agents/*.py`. Run either standalone via `docker compose` (single-factory mode) or under the orchestrator (multi-factory mode).

Compose examples for both modes live in the orchestrator repo's docs.

## License

`teenyfactories` (this repo): **MIT** — see [`LICENSE`](LICENSE).

The orchestrator and any factory in `factories/<name>/` is licensed per its own repo. The orchestrator is currently proprietary with a free option; individual factories choose their own license.
