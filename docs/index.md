# TeenyFactories

**An open-core framework for distributed AI agent factories.**

A *factory* is a small, self-contained system of AI agents that work together on one problem domain — underwriting insurance, triaging support mail, drafting sales outreach. You describe the agents in Python and the UI in YAML; the platform runs each agent as its own container, drives them with a shared state store, and renders a live dashboard.

```python
import teenyfactories as tf

# An agent: subscribe to a state, do work, advance the state.
@tf.on_state('documents', 'loaded').do
def analyse(item):
    summary = tf.llm().ask("Summarise this document: {doc}", {"doc": item['data']['text']})
    tf.collection('documents').set(item['key'], state='analysed',
                                   data={**item['data'], 'summary': summary})

while True:
    tf.run_pending()
    tf.sleep(1)
```

## The three parts

| Part | What it is | License |
|---|---|---|
| **Core** (`teenyfactories`) | The Python library you import as `tf`. Multi-provider LLMs, the state-driven pub/sub store, embeddings, MCP tools, bucket storage. | **MIT** (this repo) |
| **Factories** | *Your* code. One per problem domain — a `factory.yml` plus one Python file per agent. | Yours |
| **Orchestrator** | The app that discovers factories, spawns agent containers, and serves the UI / chat / state-graph editor. | Proprietary (separate repo) |

Run a factory **standalone** with `docker compose` (single-factory mode), or under the **orchestrator** (multi-factory mode with the full UI).

## The core idea: one primitive, one collection per lifecycle

Everything in a factory flows through a single primitive — a **state on a row** in a shared `factory_data` store:

- Writing a row with a `state` fires a notification on a channel named `{factory}.{collection}.{state}`.
- Agents **subscribe** with `tf.on_state(collection, state).do(handler)` — with startup replay, so nothing queued while an agent was down is lost.
- An agent's job is to **consume** a row and **advance** it to the next state. That's the whole lifecycle.

```python
tf.collection('documents').set('doc-1', state='loaded', data={...})  # fires `factory.documents.loaded`

@tf.on_state('documents', 'loaded').do
def handle(item):
    ...  # do work, then transition the row to a new state
```

There's no separate "workers vs agents" distinction and no message-bus to wire up. Some agents call LLMs, some don't — that's a property of what the code does, not a structural category.

## What a factory looks like

```
my-factory/
├── factory.yml          # metadata, agent definitions, the UI layout
└── agents/
    ├── ingester.py      # one Python file per agent (the slug IS the filename)
    ├── assessor.py
    └── settler.py
```

`factory.yml` declares the **states** (the lifecycle), the **agents** (each gets a container), and the **default_ui** (a dashboard built from composable components). Each `agents/*.py` is a normal Python script using `tf`.

## Where to go next

<div class="grid cards" markdown>

- :material-rocket-launch: **[Setup](setup.md)** — run the platform, install the library, create your first factory.
- :material-language-python: **[tf reference](reference/tf-index.md)** — the Python API: runtime, collections, LLMs, MCP tools, environment, volumes.
- :material-view-dashboard: **[Composable UI reference](reference/ui-index.md)** — build a dashboard from YAML: common rules + one doc per component.

</div>
