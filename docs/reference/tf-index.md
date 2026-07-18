# tf framework reference — index

The tf framework (`import teenyfactories as tf`) is the Python library factory agents use to react to data changes, call language models, and persist state. `factory.yml` defines a factory's states, agents, and volumes.

- **tf-common** — the core runtime: state-change subscriptions, the dispatch loop, error handling, logging, and the public API surface.
- **tf-data** — reading, writing, and querying rows in a factory's data store, including vector search.
- **tf-llm** — calling language models, structured output, agentic tool-calling loops, and embeddings.
- **tf-mcp** — exposing an agent's capabilities as callable tools.
- **tf-environment** — secrets, credentials, and configuration available to a running agent.
- **tf-volumes** — reading and writing files on a factory's attached storage.
- **factory-yaml** — the `factory.yml` manifest format: states, agents, and volumes.
