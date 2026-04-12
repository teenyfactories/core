"""
Chat Tool Self-Registration

Provides a decorator for factory workers to register tools that the
orchestrator's LLM chat agent can call at runtime.

Usage:
    import teenyfactories as tf

    @tf.chat_tool(
        name="query_spend",
        description="Query spend data by category",
        params={"category": "L1 category name"}
    )
    def handle_query(params):
        # Process and return result
        return {"total": 12345.67, "items": [...]}

    # After all decorators, start the chat tool listener
    tf.start_chat_tools()
"""

import json
from typing import Callable, Dict, Any, Optional, List

from .logging import log_info, log_error, log_debug
from .message_queue import send_message, on_message

# Module-level registries
_registered_tools: List[Dict[str, Any]] = []
_tool_handlers: Dict[str, Callable] = {}


def chat_tool(name: str, description: str, params: Optional[Dict[str, str]] = None):
    """
    Decorator to register a function as a chat tool.

    Args:
        name: Unique tool name (e.g. "query_spend")
        description: Human-readable description for the LLM
        params: Dict of param_name -> description for the LLM
    """
    def decorator(func: Callable) -> Callable:
        _registered_tools.append({
            'name': name,
            'description': description,
            'params': params or {},
        })
        _tool_handlers[name] = func
        log_info(f"Registered chat tool: {name}")
        return func
    return decorator


def _handle_chat_tool_request(message):
    """Handle incoming chat tool requests from the orchestrator."""
    data = message.get('data', {})
    tool_name = data.get('tool_name')
    correlation_id = data.get('correlation_id')
    params = data.get('params', {})

    if not tool_name or not correlation_id:
        log_error(f"Invalid chat tool request: missing tool_name or correlation_id")
        return

    handler = _tool_handlers.get(tool_name)
    if not handler:
        log_error(f"Unknown chat tool: {tool_name}")
        send_message('chat_tool_response').with_data({
            'correlation_id': correlation_id,
            'error': f'Unknown tool: {tool_name}',
        })
        return

    try:
        log_debug(f"Executing chat tool: {tool_name} with params: {params}")
        result = handler(params)

        # Ensure result is JSON-serializable
        if not isinstance(result, (dict, list, str, int, float, bool, type(None))):
            result = str(result)

        send_message('chat_tool_response').with_data({
            'correlation_id': correlation_id,
            'result': result,
        })
        log_info(f"Chat tool {tool_name} completed successfully")

    except Exception as e:
        log_error(f"Chat tool {tool_name} failed: {e}")
        send_message('chat_tool_response').with_data({
            'correlation_id': correlation_id,
            'error': str(e),
        })


def start_chat_tools():
    """
    Publish the tool catalog and start listening for tool requests.
    Call this after all @chat_tool decorators have been registered.
    """
    if not _registered_tools:
        log_info("No chat tools registered, skipping chat tool startup")
        return

    # Publish catalog so the orchestrator knows what tools are available
    send_message('chat_tool_catalog').with_data({
        'tools': _registered_tools,
    })
    log_info(f"Published chat tool catalog: {[t['name'] for t in _registered_tools]}")

    # Subscribe to tool requests
    on_message('chat_tool_request').do(_handle_chat_tool_request)
    log_info("Listening for chat tool requests")
