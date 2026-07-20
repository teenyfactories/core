# tf-llm — LLM calls, structured output, agent loops, embeddings

> **Cross-doc refs:** Provider/model env vars in [tf-environment](id:tf-environment); data writes in [tf-data](id:tf-data).

## LLM Calls

`tf.llm()` is a **config-first fluent builder** with eager terminals (the call fires when you call a terminal). Configure model/provider, then end with a terminal:

```python
import teenyfactories as tf

answer = tf.llm().ask("Summarise: {text}", {"text": data})

answer = (
    tf.llm()
    .system("You are a terse analyst.")
    .temperature(0.1)
    .max_tokens(8192)
    .ask("Analyse: {text}", {"text": data})
)
```

`.ask(prompt, inputs)` fills `{placeholders}` from `inputs`. System prompt is literal (not templated); only human prompt is templated.

### Config links

| Link | Effect |
|---|---|
| `.model(name)` | Override `DEFAULT_LLM_MODEL` for this call. |
| `.provider(name)` | Override provider: `openai` / `anthropic` / `google` / `ollama` / `azure_bedrock` / `digitalocean` / `openrouter`. |
| `.temperature(t)` | Sampling temperature (dropped for reasoning-class models). |
| `.max_tokens(n)` | Output token cap. Single-shot `.ask*` omits it for provider default. **Agentic loop applies 8192 default** to avoid truncation. |
| `.system(text)` | System prompt (literal). |
| `.with_structured_output(Model)` | Return parsed Pydantic instance instead of text. |

### Terminals (eager)

| Terminal | Returns |
|---|---|
| `.ask(prompt, inputs)` | `output` (str or structured Model) |
| `.ask_with_meta(prompt, inputs)` | `(output, meta)` tuple |
| `.run_agent_loop(task)` | `output` (str) from agentic loop |
| `.run_agent_loop_with_meta(task)` | `(output, meta)` from loop |

### Structured output

`.with_structured_output(Model)` returns parsed Pydantic instance. Prefers **native provider enforcement** (LangChain `with_structured_output`), falls back to a `PydanticOutputParser` when the provider/model doesn't enforce.

**Do NOT put a `{format_instructions}` placeholder in your prompt.** The framework handles format injection itself — on the fallback path it appends and fills format instructions automatically. Adding the placeholder yourself breaks the preferred native path (native mode leaves it unfilled), forcing the fallback on every call. Write your prompt as plain instructions; let `with_structured_output(Model)` do the rest.

```python
from pydantic import BaseModel, Field

class Analysis(BaseModel):
    result: str
    score: float

out = tf.llm().with_structured_output(Analysis).ask("Analyse: {text}", {"text": data})
```

### The `meta` dict

`.ask_with_meta()` and `.run_agent_loop_with_meta()` return `(output, meta)` where `meta` is:

```python
meta = {
    "provider":      "anthropic",
    "model":         "claude-...",
    "cost":          0.0021,        # USD — OpenRouter-only
    "finish_reason": "stop",        # provider verbatim
    "latency_ms":    842,
    "usage":         {...},         # LangChain usage_metadata
    "raw":           {...},         # verbatim provider blob
}
```

**Loop-level signals** (`.run_agent_loop_with_meta()` only):
- `meta["turns"]` — per-turn dicts with `{turn, tool_calls, usage}`
- `meta["tool_calls"]` — flat list `[{name, args, result}, ...]`
- `meta["stop_reason"]` — `'completed'` | `'max_turns'` | `'length'` | `'repeat_error'` | `'error'`
- `meta["error"]` — exception msg if `stop_reason == 'error'`, else None
- `meta["usage"]` — folded across all turns (summed tokens, turn count)

### Per-call model override

Override container defaults for one call:

```python
answer = (
    tf.llm()
    .provider("anthropic")
    .model("claude-haiku-4-5-20251001")
    .temperature(0.1)
    .ask("Classify: {text}", {"text": data})
)
```

Omit to use `DEFAULT_LLM_PROVIDER` / `DEFAULT_LLM_MODEL` env vars.

**Temperature dropped for reasoning models** (Opus 4.7+). Framework detects by model ID; `.temperature(...)` is harmless but has no effect.

### Agentic loop (`.run_agent_loop*`)

`.run_agent_loop(task)` runs native provider tool-calling ReAct loop — model calls tools, sees results, continues until stop or `max_turns`. Loop is eager (tools mutate state).

```python
out, meta = (
    tf.llm()
    .system(SYSTEM_PROMPT)
    .add_tools_from_self()           # bind THIS agent's MCP tools
    .max_turns(50)
    .run_agent_loop_with_meta(task)
)
```

**Tool sourcing** (same-factory only):
- `.add_tools_from_self()` — this agent's own `tf.add_mcp_tool` handlers (local)
- `.add_tools_from_agent(name)` — another agent's published tools (over wire)
- `.add_tool(fn_or_name)` — single callable or named MCP tool

Cross-factory tool binding unavailable (security gated).

**Loop controls:**
- `.max_turns(n)` — runaway guard (default 50), stop with `stop_reason='max_turns'`
- `.max_tokens(n)` — per-turn cap; **omitted defaults to 8192** (not provider default)
- `.on_turn(cb)` — callback after each turn with `{turn, tool_calls, usage}` where `tool_calls` is a list of tool NAMES; full `{name, args, result}` dicts are in the run `meta['tool_calls']`
- `.inject_tool_args(mapping, tools=None)` — force args into tool calls at dispatch

**Provider support:** `.run_agent_loop*` requires native `bind_tools` support (checked per client).

**Reliability (built-in):** Serial multi-tool dispatch, arg validation fed back to model, tool errors returned as results, bounded history + result caps, API retry/backoff, 8192-token per-turn default, truncation-aware (stops at length), repeat-error guardrail, partial progress on failure.

### Prompt caching

Caching always on — no opt-in. Inside `.run_agent_loop*`, provider-dependent:

- **Anthropic** — system block cached statically, last message cached rolling per turn; two breakpoints within Anthropic's limit of four. History trimmed with high/low watermark to keep cache byte-stable.
- **OpenRouter/OpenAI** — prefix-cache automatic per upstream; loop reuses one client every turn. Caller's stable `extra_body` rides unchanged. No auto-pin of served upstream.
- **Others** (Google/Ollama/Azure Bedrock) — no-op.

**Cache floor (Anthropic):** breakpoint only caches past ~1024 tokens (Sonnet/Opus) or ~2–3k (Haiku). Below floor, `cache_read`/`cache_creation` stay at 0.

**Known gap:** single-shot `.ask()` / `.ask_with_meta()` apply **no** caching. Factory-side supply for big stable prefixes.

### LEGACY: `tf.call_llm()`

`tf.call_llm(prompt, inputs, response_model=...)` is legacy single-shot API, kept as thin shim. Works byte-for-byte, logs debug deprecation breadcrumb. **Migrate to `tf.llm().ask()` / `.ask_with_meta()`:**

| Legacy | New |
|---|---|
| `tf.call_llm(prompt, inputs)` | `tf.llm().ask(prompt, inputs)` |
| `tf.call_llm(..., response_model=M)` | `tf.llm().with_structured_output(M).ask(...)` |
| `tf.call_llm(..., provider=p, model=m, temperature=t, max_tokens=n)` | `tf.llm().provider(p).model(m).temperature(t).max_tokens(n).ask(...)` |

**Usage logging & cost:** Every `tf.llm()` / `tf.call_llm()` / `tf.embed()` transparently logged to `factory_ai_usage` with `raw` blob (verbatim provider metadata). **Cost NOT computed tf-side** — orchestrator computes USD at read time from `raw`. Store provider usage verbatim; never flatten token counts at write. Logging failures never break underlying call.

**Spend limits (soft):** Orchestrator enforces via `:8998` clearance channel. Agent pauses (sleeps until reset, SIGTERM-aware) if limit breached, never errors. Entirely automatic.

## Embeddings

`tf.embed(text)` is **input-first and LAZY** — result is the vector (list subclass), computes on first value-access, then memoises. Configure fluently:

```python
vector = tf.embed("some text")                              # single → single vector
vectors = tf.embed(["text 1", "text 2", "text 3"])         # batch → list of vectors
vector = tf.embed("query").provider("openrouter").model("baai/bge-m3")
vector, meta = tf.embed("query").with_meta()               # (vector, meta) tuple
```

`.with_meta()` returns `(vector, meta)` with same shape as LLM meta (`{provider, model, cost, finish_reason, latency_ms, usage, raw}`; `finish_reason` always None, `cost` OpenRouter-only).

LEGACY: eager kwargs form `tf.embed(text, provider=p, model=m)` still works, logs debug deprecation. **Migrate to fluent form**.

**Integration:** Embeddings auto-flow into `factory_vectors` when you pass `embedding=` to `tf.collection(...).set(...)` / `.add(...)`. Vector search via `tf.collection(...).vector_search(...)`.

**Provider config:** `DEFAULT_EMBEDDING_PROVIDER` (`openai`, `ollama`, `openrouter`), `DEFAULT_EMBEDDING_MODEL`. OpenRouter reuses `OPENROUTER_API_KEY` (one key for LLM + embeddings), returns routed cost (USD).

**Dimension constraint:** `factory_vectors` fixed-dim columns: **256 / 512 / 768 / 1024 / 1536 / 3072**. Model output must match one. Supported OpenRouter models:

| Model | Dim | Fits? |
|---|---|---|
| `baai/bge-m3` | 1024 | ✅ (recommended) |
| `openai/text-embedding-3-small` | 1536 | ✅ |
| `qwen/qwen3-embedding-8b` | 4096 | ❌ (needs new column) |
