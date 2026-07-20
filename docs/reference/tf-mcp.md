# tf-mcp — exposing factory tools to the chat agent

```python
tf.add_mcp_server(name='my-tools', description='Description of what these tools do')

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

# Order of add_mcp_server() vs add_mcp_tool() doesn't matter.
# On the first tf.run_pending() tick, the core:
#   1. Writes a row to `_mcp_tool_catalog` (key = AGENT_NAME).
#      - data = {server, tools} if MCP is configured
#      - data = {} if no tools were registered (explicit signal)
#   2. Subscribes via tf.on_state('_mcp_{tool_name}', 'request')
#      to each tool's dedicated inbox collection.
```

## Tool annotations (`.with_annotations`)

`.with_annotations({...})` attaches the standard MCP `ToolAnnotations` object to the tool. It is optional — but **declare it on every externally exposed tool**: external clients (claude.ai) use annotations to bucket tools, and an unannotated tool lands in a junk "Other tools" bucket. The dict is passed through verbatim to MCP clients (no validation in tf; use spec keys only).

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

## How tool calls flow

Each tool has its own collection `_mcp_{tool_name}`. A call is a single row whose state transitions:

| Step | Actor | What |
|---|---|---|
| 1 | orchestrator backend | INSERT row `(collection=_mcp_{tool}, key=correlation_id, state='request', data={agent, params})` |
| 2 | agent's `on_state('_mcp_{tool}', 'request')` handler | Sees the row. Checks `data.agent == AGENT_NAME`; if not, silently skips. |
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
