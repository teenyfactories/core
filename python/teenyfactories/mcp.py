"""
MCP Tool Registration

Allows factory workers to expose tools to the orchestrator's LLM chat agent
via MCP (Model Context Protocol) format. Tools are cataloged through
factory_states and requests are routed through the same pub/sub mechanism.

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

    # Order of add_mcp_server() vs add_mcp_tool() doesn't matter.
    # Catalog is published on first tf.run_pending() call.
"""

import json
from typing import Callable, Dict, Any, Optional, List

from .logging import log_info, log_error, log_debug

# Module-level registries
_mcp_server: Optional[Dict[str, str]] = None
_mcp_tools: List[Dict[str, Any]] = []
_mcp_handlers: Dict[str, Callable] = {}
_mcp_pending: bool = False
_mcp_published: bool = False


class McpToolBuilder:
    """Fluent builder for tf.add_mcp_tool('name', 'description').with_input({...}).do(handler)"""

    def __init__(self, name: str, description: str):
        self._name = name
        self._description = description
        self._input_schema = {"type": "object", "properties": {}}

    def with_input(self, schema: dict):
        """Set the JSON Schema for this tool's input parameters."""
        self._input_schema = schema
        return self

    def do(self, handler: Callable):
        """Register the handler function for this tool."""
        _mcp_tools.append({
            'name': self._name,
            'description': self._description,
            'inputSchema': self._input_schema,
        })
        _mcp_handlers[self._name] = handler
        log_info(f"Registered MCP tool: {self._name}")
        return handler


def add_mcp_tool(name: str, description: str) -> McpToolBuilder:
    """Register an MCP tool. Call before or after add_mcp_server() — order doesn't matter.

    Usage:
        tf.add_mcp_tool('query_spend', 'Query spend data') \\
            .with_input({"type": "object", "properties": {...}}) \\
            .do(handler_function)
    """
    return McpToolBuilder(name, description)


def add_mcp_server(name: str, description: str = ''):
    """Declare the MCP server metadata. Catalog is published on first run_pending() call.

    Usage:
        tf.add_mcp_server(name='spend-data', description='Spend analysis tools')
    """
    global _mcp_server, _mcp_pending
    _mcp_server = {'name': name, 'description': description}
    _mcp_pending = True
    log_info(f"MCP server declared: {name}")


def _maybe_publish_mcp():
    """Called by run_pending() on first tick. Publishes catalog and starts listener.
    This is deferred so that order of add_mcp_server() vs add_mcp_tool() doesn't matter."""
    global _mcp_pending, _mcp_published

    if not _mcp_pending or _mcp_published:
        return
    if not _mcp_server:
        return
    if not _mcp_tools:
        log_info("MCP server declared but no tools registered, skipping publish")
        return

    _mcp_pending = False
    _mcp_published = True

    # Import here to avoid circular imports
    from .message_queue import send_message, on_message

    # Publish catalog so the orchestrator knows what tools are available
    send_message('mcp_tool_catalog').with_data({
        'server': _mcp_server,
        'tools': _mcp_tools,
    })
    log_info(f"Published MCP catalog: {_mcp_server['name']} ({len(_mcp_tools)} tools: {[t['name'] for t in _mcp_tools]})")

    # Subscribe to tool requests
    on_message('mcp_tool_request').do(_handle_mcp_request)
    log_info("Listening for MCP tool requests")


def _handle_mcp_request(message):
    """Handle incoming MCP tool requests from the orchestrator."""
    from .message_queue import send_message

    data = message.get('data', {})
    tool_name = data.get('tool_name')
    correlation_id = data.get('correlation_id')
    params = data.get('params', {})

    if not tool_name or not correlation_id:
        log_error("Invalid MCP tool request: missing tool_name or correlation_id")
        return

    handler = _mcp_handlers.get(tool_name)
    if not handler:
        log_error(f"Unknown MCP tool: {tool_name}")
        send_message('mcp_tool_response').with_data({
            'correlation_id': correlation_id,
            'error': f'Unknown tool: {tool_name}',
        })
        return

    try:
        log_debug(f"Executing MCP tool: {tool_name}")
        result = handler(params)

        # Ensure result is JSON-serializable
        if not isinstance(result, (dict, list, str, int, float, bool, type(None))):
            result = str(result)

        send_message('mcp_tool_response').with_data({
            'correlation_id': correlation_id,
            'result': result,
        })
        log_info(f"MCP tool {tool_name} completed")

    except Exception as e:
        log_error(f"MCP tool {tool_name} failed: {e}")
        send_message('mcp_tool_response').with_data({
            'correlation_id': correlation_id,
            'error': str(e),
        })
