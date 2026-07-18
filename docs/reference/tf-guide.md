# tf guide — picking the right chunk

These are chunks of the tf framework reference (Python `import teenyfactories as tf`) plus the `factory.yml` manifest schema. Fetch the chunk for the job at hand — most agent code only touches two or three of these.

## Quick-map

Writing a state handler / choosing a primitive / debugging a stuck row → `tf-common` · reading, writing, or querying rows → `tf-data` · calling a model, structured output, agentic tool loops, embeddings → `tf-llm` · exposing an agent's own tools to the chat layer → `tf-mcp` · secrets, API keys, identity/config env vars → `tf-environment` · reading/writing files on a factory volume → `tf-volumes` · shaping or editing `factory.yml` → `factory-yaml`.

## Chunks

**tf-common** — the runtime core: `tf.on_state(collection, state).do(handler)` pub/sub, the poll/NOTIFY dispatch loop, the 5-strike-then-park failure policy, logging, `tf.breakpoint()` stepped debugging, collection/state naming validation, the main-loop and shutdown shape, the full public `tf` API surface, the agent `.py` docstring convention. Start here for anything about how an agent reacts to state changes, or why a row isn't being processed.

**tf-data** — the `factory_data` API: `.set()`/`.add()` writes (Python `data=` REPLACES the value, unlike the REST `PUT`'s shallow merge — this is the single most load-bearing warning in the library), the lazy query builder (`.state()`, `.where()` predicate DSL, `.get_all()`/`.count()`/`.first()`), `.remove()`, `.vector_search()`, `tf.embed()`-backed writes, and the orchestrator's parallel REST surface (what the composable UI's `data:` bindings actually call). Reach for this whenever code reads or writes rows.

**tf-llm** — the `tf.llm()` fluent builder: config links (`.model()`, `.provider()`, `.temperature()`, `.system()`, `.with_structured_output()`), terminals (`.ask()`, `.ask_with_meta()`, `.run_agent_loop()`), the agentic tool-calling loop (tool sourcing, `.max_turns()`, provider support matrix), prompt caching behavior, usage/cost logging, and `tf.embed()`. Also documents the LEGACY `tf.call_llm()` shim and its migration path — don't write new code against it. Reach for this for any model call, structured extraction, or tool-using loop.

**tf-mcp** — how an agent exposes its own capabilities as callable tools to the chat/LLM layer: `tf.add_mcp_server()`, `tf.add_mcp_tool(...).with_input(...).with_annotations(...).do(handler)`, tool annotations, and the request/response row protocol behind a call (a `_mcp_{tool}` collection under the same `on_state` contract as `tf-common` — the same strike/park rules apply if a handler hangs). Reach for this when a factory needs a chat-callable action beyond a GUI button.

**tf-environment** — `tf.secrets(key, default=)` (read-only, scope-chained, fail-open — never raises, unlike `tf-volumes`' fail-loud policy), identity vars (`tf.FACTORY_NAME`, `AGENT_NAME`, `AGENT_SLUG`), the live config-resolution cascade for LLM/embedding provider settings (UI-edited, no restart needed, ~45s cache), and the full container env-var inventory. Reach for this to resolve credentials/config or to look up what an env var does.

**tf-volumes** — `tf.bucket_store(name)`, the file-handle API for factory-declared volumes: `list()`, `read()`, `open()`, `write()`, `delete()`, `exists()`, `url()`, its exception hierarchy, and the docker-vs-k8s backend split (local bind-mount vs proxied S3 — behavior differs per method, e.g. `delete()` on a missing key raises on docker but is idempotent on k8s, and `url()` isn't available on k8s yet). Reach for this for any file I/O on a factory volume.

**factory-yaml** — the `factory.yml` schema: top-level keys, banned/deprecated keys (`name:`, `version:`, `status:`, `docker:`, `workers:`), the `states:` map and its **display-label-vs-runtime-slug** mapping (the doc's own flagged #1 cause of dead factories — a Title-Case `"Collection: State"` label maps to a lowercase, non-alnum-collapsed Python slug), the `agents:` map, volume definitions vs per-agent attachments, `.teenyfactories/` tenant-local overrides, and rename cascade semantics. Reach for this whenever you're shaping or editing the manifest itself, and always when translating a display label to the slug used in Python code.
