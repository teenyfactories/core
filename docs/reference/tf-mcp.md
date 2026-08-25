# tf-mcp — exposing factory tools to the chat agent

```python
tf.add_mcp_server(description='Description of what these tools do')

def my_handler(params):
    # params is a dict matching the inputSchema
    return {"result": "data"}  # must be JSON-serializable

tf.add_mcp_tool('tool_name', 'Description for the LLM') \
    .with_input({
        "type": "object",
        "properties": {
            "param_name": {
                "type": "string",
                "description": "What this param does",
                "enum": ["option1", "option2"]  # optional
            }
        },
        "required": ["param_name"]
    }) \
    .with_annotations({"readOnlyHint": True, "openWorldHint": False}) \
    .do(my_handler)

# Tool NAME must match ^[a-z0-9_]{1,35}$ (lowercase letters, digits, underscore;
# 1-35 chars) — it becomes the `_mcp_<name>` collection (whose CHECK constraint is
# exactly this shape) + a Postgres NOTIFY channel, so uppercase/hyphens/spaces/
# dots would corrupt it or fail the CHECK at invoke time. A bad name is REJECTED
# at .do(): the tool is skipped and an ERROR row is written to factory_logs (the
# agent's other valid tools keep working). Use snake_case, verb-first
# (query_spend, search_claims).

# Calling add_mcp_server() is the SWITCH that publishes this agent's tools — with
# no server declared, NOTHING is published even if add_mcp_tool() ran. Its args are
# KEYWORD-ONLY: use add_mcp_server(description='…'); a bare positional
# add_mcp_server('…') raises TypeError. `description` (a one-line summary shown to
# the Foreman chat) is OPTIONAL — add_mcp_server() alone is a valid publish switch.
# (The old `name=` arg is deprecated and ignored — the agent slug + factory.yml
# title are the identity; passing name= logs a one-line deprecation notice.)
#
# Order of add_mcp_server() vs add_mcp_tool() doesn't matter.
# On the first tf.run_pending() tick, the core:
#   1. Writes a row to `_mcp_tool_catalog` (key = AGENT_SLUG, the stable
#      factory.yml agent key; falls back to AGENT_NAME only if slug wasn't injected).
#      - data = {server, tools} if MCP is configured
#      - data = {} if no tools were registered (explicit signal)
#   2. Subscribes via tf.on_state('_mcp_{tool_name}', 'request')
#      to each tool's dedicated inbox collection.
```

## Read-me-first gate (`tf.add_mcp_readme`)

Declare a mandatory briefing for an agent's tools:

```python
tf.add_mcp_readme("""
Filing conventions for this agent's tools:
- ALWAYS search before you create.
- Write anchored diff-edits, never wholesale overwrites.
""")
```

This registers a `read_me_first` tool and flags the agent's catalog with `readme_gate:
true`. The tool's **description is fixed** ("Read this FIRST…") — you supply only the body.
An agent that only calls `add_mcp_readme` (no `add_mcp_server`) still publishes.

**Per-audience bodies (`.audience([...])`).** Chain `.audience()` (same surface tokens as
tool scoping — `external` / `foreman` / `agent_loop`, alias `internal`) to serve *different*
briefing text to different surfaces — e.g. an external product how-to vs. an internal
agent-loop guide:

```python
tf.add_mcp_readme(EXTERNAL_HOWTO).audience(['external'])   # Claude-in-PowerPoint / research
tf.add_mcp_readme(INTERNAL_GUIDE).audience(['internal'])   # foreman + agent loops
```

Omit `.audience()` for one body shared by every surface. A later call with the **same**
audience replaces that body. The bodies ride on the single `read_me_first` catalog entry as
a `readme_bodies` list; each surface **serves its own body straight from the catalog** (the
briefing is static text — it is never round-tripped to the agent, so it works even when the
agent is stopped). A surface an audience explicitly names wins over the all-surfaces default.

**Enforcement differs by consumer:**

| Consumer | Behaviour |
|---|---|
| **Foreman chat** | **Gated** — the agent's other tools are blocked (per conversation) until `read_me_first` is called; the blocked call returns `"Call the _read_me_first() tool first — …"`. Orchestrator-side, keyed on the conversation session. The gate is set **only if a body targets `foreman`** — an `external`-only readme does NOT gate the foreman (it couldn't list `read_me_first` to ack it, which would deadlock the agent's other tools). |
| **External MCP clients** (`/api/mcp`) | **Not gated** — the fixed description instructs them to read it first (soft). Served the `external` body. |
| **Cross-agent LLM loop** (`add_tools_from_agent`) | **Not gated** — `read_me_first` is simply available in the bound set; served the `agent_loop` body. |

**Convention:** the readme is the CANONICAL "how to use these tools" text. An agent's own
system prompt should **defer to it, not restate it** (avoids two drifting sources of truth).

**Constraint — one gated agent per factory (for now).** The foreman chat flattens every
agent's tools into one namespace keyed by bare tool name (last-wins). `read_me_first` is a
fixed name, so if two agents in the same factory both call `add_mcp_readme`, one agent's
`read_me_first` collides away and that agent's tools can never be unlocked in chat. Keep the
readme on a single agent per factory until per-agent namespacing lands (e.g. the intelligence
factory keeps `read_me_first` on the librarian, not on each ops agent).

## Tool annotations (`.with_annotations`)

`.with_annotations({...})` attaches the standard MCP `ToolAnnotations` object to the tool. It is optional — but **declare it on every externally exposed tool**: external clients (claude.ai) use annotations to bucket tools, and an unannotated tool lands in an "Other tools" bucket. tf forwards the dict to MCP clients verbatim (no validation; use spec keys only).

| Key | Type | MCP default if absent | Meaning |
|---|---|---|---|
| `readOnlyHint` | bool | `false` | Tool does not modify its environment |
| `destructiveHint` | bool | `true` | Tool may perform destructive updates (only meaningful when not read-only) |
| `idempotentHint` | bool | `false` | Repeated identical calls have no additional effect |
| `openWorldHint` | bool | `true` | Tool interacts with external entities (web etc.) — factory tools that only touch the factory DB should set `false` |
| `title` | str | — | Human-readable display title |

```python
# Read-only query tool
.with_annotations({"readOnlyHint": True, "openWorldHint": False})

# Write tool (non-destructive insert, not idempotent)
.with_annotations({"readOnlyHint": False, "destructiveHint": False,
                   "idempotentHint": False, "openWorldHint": False})
```

The annotations land in the tool's `_mcp_tool_catalog` entry as an optional `annotations` field (absent when not declared); the orchestrator's external MCP endpoint passes the object through to clients verbatim.

## Tool audience — publish scoping (`.audience([...])`)

A tool can be published to three surfaces: external MCP clients (`/api/mcp`), the foreman
chat, and agent LLM loops (`add_tools_from_self` / `add_tools_from_agent`). `.audience(list)`
is an author-side **whitelist** naming the surfaces a tool is published to. **Omit it and the
tool is visible on every surface** (fail-open — a sensitive tool must opt IN to a
restriction; forgetting `.audience()` never silently narrows).

| Surface token | Publishes to |
|---|---|
| `'external'` | External MCP clients (`/api/mcp`) |
| `'foreman'` | The in-built foreman chat |
| `'agent_loop'` | The LLM-loop bulk binders (`add_tools_from_self` / `add_tools_from_agent`) |
| `'internal'` | Alias for `foreman` + `agent_loop` (every non-external surface) |

```python
# internal only — foreman + loops, NOT the public API
tf.add_mcp_tool('run_fraud_sweep', 'Sweep the whole book for fraud rings') \
    .with_input({...}) \
    .audience(['internal']) \
    .do(run_sweep)

# pipeline-only — driven ONLY by direct _mcp_<name> state-writes, no LLM/client surface
tf.add_mcp_tool('recompute_index', 'Rebuild the vector index') \
    .audience([]) \
    .do(recompute)
```

- An **empty** audience (`.audience([])`) leaves a tool reachable ONLY via its own
  `_mcp_<name>` request→response pipeline (any code writing that row still drives it — see
  "How tool calls flow" below).
- Omitting `'agent_loop'` from the audience affects the **bulk binders** only. An explicit
  `add_tool('name')` is a deliberate single pick and still binds — the author's own override.
- Unknown surface tokens are logged and ignored (the valid tokens are whitelisted).
- Compiles at registration to a per-tool `hidden_from: [...]` denylist in the catalog (the
  complement of the audience; absent ⇒ visible everywhere). `external`/`foreman` are enforced
  orchestrator-side (dropped from that surface's `tools/list` AND `tools/call`); `agent_loop`
  is enforced core-side in `_gather_tools`.
- This is **author-side** scoping, distinct from a credential's caller-side `tool_selection`
  allow-list.

## How tool calls flow

Each tool has its own collection `_mcp_{tool_name}`. A call is a single row whose state transitions:

| Step | Actor | What |
|---|---|---|
| 1 | orchestrator backend | INSERT row `(collection=_mcp_{tool}, key=correlation_id, state='request', data={agent, params})` |
| 2 | agent's `on_state('_mcp_{tool}', 'request')` handler | Sees the row. Checks `data.agent == AGENT_SLUG` (fallback `AGENT_NAME`); if not, silently skips. |
| 3 | agent | Executes handler; UPDATEs the same row to `state='response'` with `data.result` or `data.error`. |
| 4 | orchestrator backend | Polls the row by key until state = 'response'; returns `data.result` to the LLM. |

The tool inbox is an ordinary `(collection, state)` pipeline: collection `_mcp_{tool_name}`, the agent subscribes via `tf.on_state('_mcp_{tool_name}', 'request')`, and consumes each call by transitioning the row to `state='response'`. No special channel — same poll-based dispatch as every other subscription, and the same failure contract: a request row the handler never transitions to `response` is retried and eventually parked under the 5-strike-park rule (see tf-common).

**Audit trail:** every tool call is one row you can inspect:
```sql
SELECT key, state, value, created_at, updated_at
  FROM factory_data
 WHERE factory_name='my_factory'
   AND collection='_mcp_search_docs'
 ORDER BY updated_at DESC LIMIT 5;
```
(The DB column is still `value`; the Python surface exposes it as `data`.)
