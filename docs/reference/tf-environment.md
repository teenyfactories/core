# tf-environment — identity vars, live config resolution, env vars, secrets

## Secrets

```python
api_key = tf.secrets('ANTHROPIC_API_KEY')           # → str | None
api_key = tf.secrets('CLOSE_API_KEY', default='')   # never returns None
```

`tf.secrets()` is a **read-only** client for the orchestrator's in-built secrets store. Walks scope chain (agent → factory → global) on the orchestrator side; falls back transparently to `os.getenv(KEY)` if the key isn't in the store. Same key name in both stores — no rename map.

Resolution rules (locked):

| Orchestrator response | tf behaviour |
|---|---|
| `200` + value | returns value |
| `404` (not in any scope) | falls through to `os.getenv` |
| `503` (feature off — no master key) | falls through to env; latches for the rest of the process so subsequent calls skip the round-trip |
| `5xx` / network / 2s timeout | `log_warn` once per (key, reason); falls through to env |

Never raises. Agents stay running even if orchestrator is briefly unreachable. This **fail-open** posture (shared by the config cascade below) is the deliberate opposite of `tf.bucket_store` (see tf-volumes), whose file ops **fail loud** — they raise rather than silently falling back, because acting on the wrong file is worse than a visible error.

There is no `tf.secrets.set` / `.rotate` / `.list`. Writes happen via the admin UI (Admin → Secrets card, or per-factory popup on the factory edit page). Agents are read-only.

`tf.call_llm` resolves provider credentials via `tf.secrets()` automatically — no agent code change needed when you migrate keys from `.env` into the secrets store. Same name, same fallback chain.

The internal HTTP listener stays on the private agent network; never publish port 8998. Docker/Kubernetes-managed agents send a runtime-injected `AUTH_AGENT_TOKEN` in `X-Agent-Token`, alongside `X-Factory-Name` and stable `X-Agent-Slug`. The orchestrator validates the credential before using that identity for secrets, volumes, clearance or wakes. It derives each logical agent's token inside the existing secrets provider; agents never receive the master key. Internal MCP additionally requires its separate `Authorization: Bearer <user PAT>` and user permissions.

**Standalone exception:** `ORCHESTRATOR_MODE=none` with configured `FACTORY_NAME` trusts its private network without agent tokens. The server pins scope to that factory and rejects a different claimed factory. Agent-slug headers are unverified in this mode; any network peer can impersonate an agent. Volume attachments and MCP's user PAT still apply. A supplied invalid token is rejected rather than bypassed.

## Datastore and advisory wakes

Factory APIs stay unchanged. Bootstrap settings are infrastructure configuration, not managed-secret cascade values:

| Setting | Default | Contract |
|---|---|---|
| `FACTORY_DATA_BACKEND` | `postgres` | Durable rows, queries, vectors, claims, logs, usage and breakpoints use the selected semantic datastore |
| `FACTORY_NOTIFICATION_BACKEND` | `postgres` | Direct PostgreSQL notifications, or `orchestrator` for background SSE wakes |
| `TF_SECRETS_URL` | `http://orchestrator:8998` | Existing internal address, shared by secrets/config, clearance and orchestrator wakes |

Transaction-pooled deployments must select `orchestrator` notifications. SQL still travels directly through PgBouncer to PostgreSQL; the orchestrator does not proxy SQL. Every PostgreSQL datastore operation sets factory scope inside a short transaction. Handlers, embeddings, network calls and breakpoint waits run outside those transactions.

Wakes are coalesced, advisory factory signals, not work payloads or an activation registry. Connection/reconnection triggers catch-up; normal safety polling remains authoritative during wake denial or outage. No direct LISTEN fallback is opened in orchestrator-wake mode. Remote files and managed-only secrets still need their services. Explicit clearance `401/403` blocks the attempted LLM call; network failures retain the existing fail-open clearance policy.

## Configuration (from environment variables)

```python
tf.FACTORY_NAME   # Factory name (e.g. "strategic_spend"); doubles as NOTIFY channel prefix
tf.AGENT_NAME     # Agent display name (editable in the UI; can change)
tf.AGENT_SLUG     # Canonical agent identifier (factory.yml agents key); stable across renames
tf.AGENT_ID       # Per-container/pod hostname; distinguishes replicas of the same agent
```

`factory_logs.service_name` is written as `AGENT_SLUG` (falling back to `AGENT_NAME` only in dev runs where the orchestrator hasn't injected the slug). Query logs by slug, not display name.

**Live config resolution (no pod restart).** Internally, the framework resolves config values through a cascade: the orchestrator's in-built secrets/runtime-var store **first** (same `:8998` path `tf.secrets()` uses), then the container's `os.environ`. This means an operator can edit a value in the UI env-var table (e.g. set a GLOBAL `DEFAULT_LLM_PROVIDER=openrouter`) and **running agents pick it up at call time** — no container restart, no re-injection. Identity vars (`FACTORY_NAME`, `AGENT_NAME`, `AGENT_SLUG`, `AGENT_ID`) are *not* in the runtime-var table and always come straight from `os.environ`. The cascade is **deny-by-default** (only registered keys resolve from the table; everything else 404s and falls to env), **cached** per-process (~45 s, so reads stay off the hot path), and **fail-open** (if the orchestrator is unreachable or the feature is off, reads fall straight to `os.environ` and never block the agent). Factory authors don't call this directly — `tf.call_llm`, `tf.embed`, etc. resolve their own config this way automatically.

Available env vars in containers (any of these registered in the UI env-var table resolves live via the cascade above; otherwise the container uses the injected value):
- `FACTORY_NAME` — factory name
- `AGENT_NAME` — agent display name (mutable; UI-editable)
- `AGENT_SLUG` — canonical agent identifier from `factory.yml`; stable, machine-readable
- `TZ` — POSIX timezone for `tf.get_timestamp()` (orchestrator-supplied, defaults UTC)
- `DEFAULT_LLM_PROVIDER` — openai, anthropic, google, ollama, azure_bedrock, digitalocean, openrouter
- `DEFAULT_LLM_MODEL` — model name for LLM calls. For `openrouter`, use the provider-prefixed form (e.g. `anthropic/claude-3-opus`, `openai/gpt-4o`, `google/gemini-pro`).
- `DEFAULT_EMBEDDING_PROVIDER` — openai, ollama, openrouter
- `DEFAULT_EMBEDDING_MODEL` — model name for embeddings
- `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `DIGITALOCEAN_API_KEY`, `OPENROUTER_API_KEY`, `AZURE_BEDROCK_LLM_KEY` — API keys (`ollama` uses `OLLAMA_BASE_URL` below, not a key)
- `DIGITALOCEAN_INFERENCE_URL` — override DO Gradient AI base URL (default `https://inference.do-ai.run/v1`)
- `OPENROUTER_INFERENCE_URL` — override OpenRouter base URL (default `https://openrouter.ai/api/v1`)
- `OLLAMA_BASE_URL` — Ollama server base URL (default `http://host.docker.internal:11434`)
