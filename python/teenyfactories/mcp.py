"""
MCP Tool Registration

Factories expose tools to the orchestrator's LLM chat via dedicated collections
in factory_data:

- `_mcp_tool_catalog` — one row per agent. key=AGENT_NAME, data={server, tools}
  if MCP is configured, else {}. Written once on first `tf.run_pending()`.
- `_mcp_{toolname}` — one row per call. key=correlation_id.
  state progresses 'request' -> 'response' on the same row; the call's
  params/result/error live in the row's `data` JSONB payload.

Usage:
    import teenyfactories as tf

    tf.add_mcp_server(
        name='spend-data',
        description='Query and analyse classified spend data'
    )

    tf.add_mcp_tool('query_spend', 'Query spend data by category') \\
        .with_input({
            "type": "object",
            "properties": {
                "level": {"type": "string", "enum": ["l1", "l2", "l3"]}
            },
            "required": ["level"]
        }) \\
        .do(handle_query)

Order of add_mcp_server() vs add_mcp_tool() doesn't matter. The catalog row
is written and per-tool state subscriptions are registered on the first
`tf.run_pending()` tick.
"""

from typing import Callable, Dict, Any, Optional, List

from . import config
from .logging import log_error, log_debug

# Module-level registry
_mcp_server: Optional[Dict[str, str]] = None
_mcp_tools: List[Dict[str, Any]] = []
_mcp_handlers: Dict[str, Callable] = {}
_mcp_published: bool = False


# =============================================================================
# Public API — registration
# =============================================================================

class McpToolBuilder:
    """Fluent builder for tf.add_mcp_tool('name', 'description').with_input({...}).do(handler)"""

    def __init__(self, name: str, description: str):
        self._name = name
        self._description = description
        self._input_schema = {"type": "object", "properties": {}}
        self._annotations: Optional[Dict[str, Any]] = None

    def with_input(self, schema: dict):
        """Set the JSON Schema for this tool's input parameters."""
        self._input_schema = schema
        return self

    def with_annotations(self, annotations: dict):
        """Set the MCP ToolAnnotations object (readOnlyHint, destructiveHint,
        idempotentHint, openWorldHint, title). Passed through verbatim to MCP
        clients, which use it to categorise tools."""
        self._annotations = annotations
        return self

    def do(self, handler: Callable):
        """Register the handler function for this tool."""
        tool = {
            'name': self._name,
            'description': self._description,
            'inputSchema': self._input_schema,
        }
        if self._annotations is not None:
            tool['annotations'] = self._annotations
        _mcp_tools.append(tool)
        _mcp_handlers[self._name] = handler
        log_debug(f"🔨 Registered MCP tool: {self._name}")
        return handler


def add_mcp_tool(name: str, description: str) -> McpToolBuilder:
    """Register an MCP tool. Call before or after add_mcp_server() — order doesn't matter."""
    return McpToolBuilder(name, description)


def add_mcp_server(name: str, description: str = ''):
    """Declare the MCP server metadata. Catalog row is published on first run_pending()."""
    global _mcp_server
    _mcp_server = {'name': name, 'description': description}
    log_debug(f"🔨 MCP server declared: {name}")


# =============================================================================
# Catalog publish + per-tool subscribe (called by run_pending on first tick)
# =============================================================================

def _agent_name() -> str:
    return config.AGENT_NAME


def _maybe_publish_mcp():
    """Idempotent. Writes the catalog row and subscribes per-tool state handlers."""
    global _mcp_published
    if _mcp_published:
        return
    _mcp_published = True

    from .collection import collection
    from .message_queue import on_state

    agent_name = _agent_name()
    has_tools = bool(_mcp_server and _mcp_tools)

    if has_tools:
        catalog_value = {
            'server': _mcp_server,
            'tools': [
                {**tool, 'agent': agent_name}
                for tool in _mcp_tools
            ],
        }
    else:
        catalog_value = {}

    try:
        collection('_mcp_tool_catalog').set(
            agent_name, state='registered', data=catalog_value,
        )
    except Exception as e:
        log_error(f"🔨 Failed to write MCP catalog row: {e}")
        # Fall through so per-tool subscriptions still register

    if has_tools:
        tool_names = [t['name'] for t in _mcp_tools]
        log_debug(
            f"🔨 Published MCP catalog for agent '{agent_name}': "
            f"{len(_mcp_tools)} tools ({tool_names})"
        )
        # Subscribe to a dedicated call inbox per tool.
        for tool in _mcp_tools:
            collection = f"_mcp_{tool['name']}"
            # Pin the tool_name into the closure
            handler = _make_tool_state_handler(tool['name'])
            on_state(collection, 'request').do(handler)
            log_debug(f"🔨 MCP listening for calls on {collection}.request")
    # (Agents with no MCP tools: silent. The empty catalog row write still
    # happens above; no operator-relevant signal to surface.)


def _make_tool_state_handler(tool_name: str):
    """Build a handler closure for this tool's 'request' state transition."""
    from .collection import collection

    def handler(item):
        data = item.get('data') or {}
        key = item['key']

        # Agent routing — silently skip if this request was targeted elsewhere
        target_agent = data.get('agent')
        our_agent = _agent_name()
        if target_agent and target_agent != our_agent:
            return

        coll = collection(f"_mcp_{tool_name}")
        fn = _mcp_handlers.get(tool_name)
        if not fn:
            # Shouldn't happen given subscription only occurs for registered tools,
            # but we might still receive replay for a tool we no longer expose.
            log_error(f"🔨 No handler registered for MCP tool: {tool_name}")
            coll.set(key, state='response',
                     data={**data, 'error': f'No handler for tool {tool_name}'})
            return

        params = data.get('params', {})
        log_debug(f"🔨 Executing MCP tool: {tool_name} (correlation_id={key})")

        try:
            result = fn(params)
            if not isinstance(result, (dict, list, str, int, float, bool, type(None))):
                result = str(result)
            coll.set(key, state='response', data={**data, 'result': result})
            log_debug(f"🔨 MCP tool {tool_name} completed (correlation_id={key})")
        except Exception as e:
            log_error(f"🔨 MCP tool {tool_name} failed: {e}")
            coll.set(key, state='response', data={**data, 'error': str(e)})

    return handler
