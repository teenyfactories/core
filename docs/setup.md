# Setup

This page gets you from zero to a running factory. There are two ways to run: **standalone** (one factory, just `docker compose`) and **under the orchestrator** (many factories, full UI). Both share the same factory code.

## Prerequisites

- **Docker** + **Docker Compose** — every agent runs as a container.
- **Python 3.11+** — only if you want to develop/lint agent code locally (the runtime is the container image).
- An **LLM provider key** for any agent that calls `tf.llm()` (OpenAI, Anthropic, Google, Azure Bedrock, or a local Ollama).

## Install the library

The `teenyfactories` library is on PyPI (pre-release):

```bash
pip install teenyfactories
```

You rarely install it by hand for running factories — the agent base image `ghcr.io/teenyfactories/agent:dev` ships with it pre-installed. Install it locally for editor autocomplete, type-checking, and tests.

## The agent base image

Every agent container runs on `ghcr.io/teenyfactories/agent:dev`, which has the library and all Python dependencies baked in. Your agent script is mounted at `/app/script.py`; nothing else mounts by default. This means agent containers start fast and your factory repo stays tiny (no `requirements.txt`, no build step for the common case).

## Create a factory

A factory is a directory with a `factory.yml` and an `agents/` folder:

```
hello-factory/
├── factory.yml
└── agents/
    └── greeter.py
```

**`factory.yml`** — declares the lifecycle states, the agents, and the UI:

```yaml
title: Hello Factory
description: A minimal example factory.
icon: hand-wave

states:
  'Greeting: requested':
    description: A new greeting to generate.
    schema:
      type: object
      properties:
        name: { type: string }
  'Greeting: done':
    description: The generated greeting.
    schema:
      type: object

agents:
  greeter:
    name: Greeter
    description: Turns a requested greeting into a friendly message.
    input_states: ['Greeting: requested']
    output_states: ['Greeting: done']

default_ui:
  layout:
    component: tabs
    children:
      - { component: tab, slot: tab, title: Greetings }
      - component: table
        slot: panel
        data: { collection: greeting, state: done }
        config:
          columns:
            - { field: name, label: Name }
            - { field: message, label: Message }
```

**`agents/greeter.py`** — one Python file, the slug (`greeter`) is the filename:

```python
import teenyfactories as tf

@tf.on_state('greeting', 'requested').do
def greet(item):
    name = item['data'].get('name', 'world')
    message = tf.llm().ask("Write a one-line friendly greeting for {name}.",
                           {"name": name})
    tf.collection('greeting').set(item['key'], state='done',
                                  data={'name': name, 'message': message})

while True:
    tf.run_pending()
    tf.sleep(1)
```

!!! note "Collection vs state naming"
    States are written `'<Collection>: <state>'` in `factory.yml` (e.g. `'Greeting: requested'`), but in agent code you reference the collection in lowercase (`tf.collection('greeting')`, `tf.on_state('greeting', 'requested')`).

## Run it

### Under the orchestrator (recommended — full UI)

The orchestrator discovers every factory in your `factories/` directory, spawns a container per agent, and serves the dashboard.

```bash
cd orchestrator
docker compose up --build      # → http://localhost:3222
```

Drop `hello-factory/` into the `factories/` directory the orchestrator scans, and it appears in the sidebar with the UI from its `default_ui`.

### Standalone (single factory)

A factory can also run on its own via a small `docker compose` file (one service per agent, all on the base image, sharing a Postgres). Compose examples for single-factory mode live in the orchestrator repo's docs.

## Configuration

Agents read configuration from environment variables. The most common:

| Variable | Purpose |
|---|---|
| `DEFAULT_LLM_PROVIDER` | `openai` · `anthropic` · `google` · `ollama` · `azure_bedrock` · `digitalocean` · `openrouter` |
| `DEFAULT_LLM_MODEL` | Model name (also overridable per call via `tf.llm().model(...)`) |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY` | Provider credentials |
| `DEFAULT_EMBEDDING_PROVIDER` | `openai` · `ollama` · `openrouter` |
| `DEFAULT_EMBEDDING_MODEL` | e.g. `text-embedding-3-small` |
| `FACTORY_NAME` | Set per container by the orchestrator; also the NOTIFY channel prefix |
| `AGENT_NAME` | Human-readable agent label |

The orchestrator injects `FACTORY_NAME` / `AGENT_NAME` per container; you provide the provider keys.

## Next

- **[The tf module](reference/tf-guide.md)** — the full Python API your agents use.
- **[Composable UI](reference/ui-guide.md)** — build the dashboard in `default_ui`.
