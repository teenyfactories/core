# TeenyFactories

**Multi-provider LLM and message-queue abstraction for distributed agent systems.**

TeenyFactories is a small framework for building distributed AI agents that
coordinate through a shared store. Agents subscribe to *states* on rows;
writing a row with a given state fires a notification, which fans out to
every subscriber. That's the whole paradigm.

```
tf.store('documents').set(key, value, state='loaded')

@tf.on_state('documents', 'loaded').do
def handle(item): ...
```

The framework abstracts over LLM providers (OpenAI, Anthropic, Google,
Ollama, Azure Bedrock) and message backends (PostgreSQL LISTEN/NOTIFY,
Redis pub/sub) so the same agent code runs against whichever combination
the deployment chooses.

## Implementations

| Language    | Status      | Location          | Install                       |
|-------------|-------------|-------------------|-------------------------------|
| Python      | available   | [`python/`](python/)         | `pip install teenyfactories`  |
| JavaScript  | planned     | `javascript/`     | (not yet published)           |

See each implementation's own README for install instructions, the API
surface, and quick-start examples.

## Why two implementations

Factories and agents written in one language often need to integrate with
services and tooling in another. Pinning the framework to Python means
JavaScript-native UIs, edge functions, or front-of-house components have
to bridge over HTTP. A native JS port lets the same `tf.on_state` /
`tf.send_message` paradigm run wherever it's needed, with the shared
PostgreSQL or Redis backend as the coordination layer.

The protocol — what gets written to `factory_data` rows, what NOTIFY
channels are named, how messages are framed — is the same across
languages. Implementations are interchangeable consumers.

## License

MIT — see [`LICENSE`](LICENSE).
