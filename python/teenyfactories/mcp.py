"""
MCP Tool Registration

Factories expose tools to the orchestrator's LLM chat via dedicated collections
in factory_data:

- `_mcp_tool_catalog` — one row per agent. key=AGENT_SLUG (the stable
  factory.yml key; falls back to AGENT_NAME only if the slug wasn't injected),
  data={server, tools}
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

Tool names must match `^[a-z0-9_]{1,35}$` — the name becomes a `_mcp_<name>`
collection (whose CHECK constraint is exactly this shape) + NOTIFY channel and
the external `<agent>_<name>` tool id, so a name with uppercase / hyphens /
spaces / dots / special chars, or longer than 35 chars, is rejected (logged as
an ERROR, tool skipped).
"""

import re
from typing import Callable, Dict, Any, Optional, List

from . import config
from .logging import log_error, log_debug, log_info

# A tool name becomes a `_mcp_<name>` collection, a Postgres NOTIFY channel, and
# a closure key — and the orchestrator composes the external tool name as
# `<agent>_<name>`, which MUST match ^[A-Za-z0-9_-]{1,64}$. We constrain the name
# to the STRICTER shape the `_mcp_<name>` collection CHECK already enforces
# (lowercase alnum + underscore, ≤35) so a name that registers here can never be
# listed-but-uncallable: previously `[a-zA-Z0-9_-]{1,64}` let a name like
# `Query-Spend` into the catalog, then the `_mcp_Query-Spend` write failed the DB
# CHECK at invoke time. A name with uppercase, hyphens, spaces, dots, or other
# special chars — or over 35 chars — is rejected. Compiled once.
_TOOL_NAME_PATTERN = r'^[a-z0-9_]{1,35}$'
_TOOL_NAME_RE = re.compile(_TOOL_NAME_PATTERN)

# Publish surfaces a tool can be scoped to via .audience() (a whitelist). The
# wire format stays a `hidden_from` DENYLIST (absent ⇒ visible everywhere) so
# the orchestrator consumers are untouched — .audience() just compiles the
# whitelist down to its complement at .do() time.
#   external   — external MCP clients (/api/mcp): Claude-in-PowerPoint, research
#   foreman    — the in-built per-factory chat
#   agent_loop — bulk-bound into agent LLM loops (add_tools_from_self/agent)
_MCP_SURFACES = {'external', 'foreman', 'agent_loop'}
# Ergonomic alias: 'internal' = every non-external surface.
_MCP_SURFACE_ALIASES = {'internal': {'foreman', 'agent_loop'}}

# Module-level registry
_mcp_server: Optional[Dict[str, str]] = None
_mcp_tools: List[Dict[str, Any]] = []
_mcp_handlers: Dict[str, Callable] = {}
_mcp_published: bool = False

# read_me_first gate — set by add_mcp_readme(). The orchestrator's Foreman chat blocks
# this agent's OTHER tools until read_me_first is called (external MCP clients + agent
# LLM loops are NOT gated). Enforcement is entirely orchestrator-side; core only
# declares the fixed tool + the readme_gate flag in the catalog.
_mcp_readme_gate: bool = False
_README_TOOL_NAME = 'read_me_first'
_README_TOOL_DESC = (
    'Read this FIRST, before calling any other tool on this agent — it explains how '
    "this agent's tools work and how to use them correctly."
)


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
        # Author-side publish scoping — a WHITELIST of surfaces (see .audience()).
        # None ⇒ never scoped ⇒ visible on every surface (the fail-open default).
        # A set ⇒ the tool is published ONLY to those surfaces.
        self._audience: Optional[set] = None

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

    def audience(self, surfaces):
        """Whitelist the publish surfaces for this tool (replaces the retired
        hide_from_* methods — a whitelist, not a denylist).

        Surfaces: 'external' (external MCP clients, /api/mcp), 'foreman' (the
        in-built factory chat), 'agent_loop' (bulk-bound into agent LLM loops via
        add_tools_from_self / add_tools_from_agent). Convenience alias 'internal'
        = foreman + agent_loop. Accepts a list (or a bare string).

        Omit .audience() entirely for the default: visible on EVERY surface
        (fail-open — sensitive tools must opt IN to a restriction). An explicit
        empty list (`.audience([])`) publishes to NO LLM/client surface — the tool
        is then driven ONLY by direct _mcp_<name> state-writes. An explicit
        add_tool('name') in a loop is a deliberate single pick and still binds even
        if 'agent_loop' is not in the audience."""
        if isinstance(surfaces, str):
            surfaces = [surfaces]
        resolved: set = set()
        for s in (surfaces or []):
            if s in _MCP_SURFACE_ALIASES:
                resolved |= _MCP_SURFACE_ALIASES[s]
            elif s in _MCP_SURFACES:
                resolved.add(s)
            else:
                log_error(
                    f"🔨 Unknown MCP audience {s!r} on tool {self._name!r}: valid "
                    f"surfaces are {sorted(_MCP_SURFACES)} (+ alias 'internal'). "
                    'Ignored.'
                )
        self._audience = resolved
        return self

    def do(self, handler: Callable):
        """Register the handler function for this tool.

        The tool `name` must match `^[a-z0-9_]{1,35}$`. `.do()` is the
        commit point (where the tool lands in the catalog + gets its state
        subscription), so the guard lives here: a bad name never registers.
        """
        # Log-and-skip rather than raise: registration runs at agent import /
        # first-tick time, and crashing the agent over one bad tool name would
        # take down every other (valid) tool and handler it hosts. A loud
        # ERROR row in factory_logs surfaces the mistake; the tool is simply
        # not exposed. Guards the `_mcp_<name>` collection / NOTIFY channel.
        if not isinstance(self._name, str) or not _TOOL_NAME_RE.match(self._name):
            log_error(
                f"🔨 Rejected MCP tool name {self._name!r}: must match "
                f"{_TOOL_NAME_PATTERN} (lowercase letters, digits, underscore; "
                f"1-35 chars). Tool NOT registered."
            )
            return handler
        tool = {
            'name': self._name,
            'description': self._description,
            'inputSchema': self._input_schema,
        }
        if self._annotations is not None:
            tool['annotations'] = self._annotations
        if self._audience is not None:
            # Compile the audience WHITELIST down to the wire's `hidden_from`
            # DENYLIST = every surface NOT whitelisted. Empty complement (audience
            # covers all surfaces) ⇒ no field ⇒ visible everywhere. Consumers:
            # 'external'/'foreman' drop the tool from that surface's tools/list +
            # tools/call (orchestrator-side); 'agent_loop' drops it from the LLM
            # loop's bulk binders (core-side, _gather_tools).
            hidden = _MCP_SURFACES - self._audience
            if hidden:
                tool['hidden_from'] = sorted(hidden)
        _mcp_tools.append(tool)
        _mcp_handlers[self._name] = handler
        log_debug(f"🔨 Registered MCP tool: {self._name}")
        return handler


def add_mcp_tool(name: str, description: str) -> McpToolBuilder:
    """Register an MCP tool. Call before or after add_mcp_server() — order doesn't matter.

    `name` must match `^[a-z0-9_]{1,35}$` (lowercase letters, digits,
    underscore; 1-35 chars). It becomes a `_mcp_<name>` collection + Postgres
    NOTIFY channel and is composed into the external tool name `<agent>_<name>`,
    so a name with uppercase / hyphens / spaces / dots / special chars would
    corrupt the channel or fail the collection's CHECK at invoke time. A name
    that fails validation is logged as an ERROR to factory_logs at `.do()` time
    and the tool is NOT registered (the agent keeps running).
    """
    return McpToolBuilder(name, description)


def add_mcp_server(*, description: str = '', name: str = None):
    """Declare the MCP server — the publish SWITCH for this agent's tools.

    Both args are KEYWORD-ONLY; a bare positional `add_mcp_server('desc')` raises
    TypeError. `description` (a one-line summary shown to the Foreman chat) is
    OPTIONAL, so `add_mcp_server()` alone is a valid publish switch. The catalog row
    is published on the first run_pending() tick.
    """
    global _mcp_server
    # LEGACY: `name=` is retired — the agent slug (factory.yml key) + display title
    # are the identity, so a passed name is ignored (just logged). Remove this param
    # once no factory calls add_mcp_server(name=...). Pre-stable.
    if name is not None:
        log_info(
            f"🔨 add_mcp_server(name={name!r}) is deprecated and ignored — the agent "
            'slug + factory.yml title are the identity. Drop the name= argument.'
        )
    _mcp_server = {'description': description}
    log_debug('🔨 MCP server declared')


def add_mcp_readme(body: str):
    """Declare a mandatory read-me-first briefing for this agent's tools.

    Registers a fixed-name `read_me_first` tool (returns `body` verbatim) and flags
    the agent's catalog with `readme_gate: true`. In the Foreman chat the agent's
    OTHER tools are blocked until read_me_first is called (enforced orchestrator-side,
    per conversation); external MCP clients and agent LLM loops are NOT gated — they
    just see the tool with its fixed "read this first" description. One per agent;
    a second call replaces the body. Also acts as a publish switch — an agent that
    only calls add_mcp_readme (no add_mcp_server) still publishes.
    """
    global _mcp_readme_gate
    # Dedupe: replace an existing read_me_first body rather than twin the tool.
    for t in [t for t in _mcp_tools if t.get('name') == _README_TOOL_NAME]:
        _mcp_tools.remove(t)
    _mcp_handlers.pop(_README_TOOL_NAME, None)
    (add_mcp_tool(_README_TOOL_NAME, _README_TOOL_DESC)
        .with_annotations({'readOnlyHint': True, 'openWorldHint': False})
        .do(lambda _params: body))
    _mcp_readme_gate = True


# =============================================================================
# Catalog publish + per-tool subscribe (called by run_pending on first tick)
# =============================================================================

def _agent_name() -> str:
    # Stable identity for the MCP catalog key + each tool's `agent` field + call
    # routing: AGENT_SLUG (the factory.yml key — lowercase-alnum/_/-), NOT
    # AGENT_NAME (the mutable display name). The orchestrator composes the
    # external tool name as `<agent>_<tool>`, which MUST match
    # ^[A-Za-z0-9_-]{1,64}$ — a display name like "Meeting Collector" carries a
    # space → an invalid MCP tool name, and renaming the display name would
    # silently re-key the whole catalog. AGENT_NAME is only a last-resort fallback
    # for the degenerate case where AGENT_SLUG wasn't injected.
    return config.AGENT_SLUG or config.AGENT_NAME


def _maybe_publish_mcp():
    """Idempotent. Writes the catalog row and subscribes per-tool state handlers."""
    global _mcp_published
    if _mcp_published:
        return
    _mcp_published = True

    from .collection import collection
    from .message_queue import on_state

    agent_name = _agent_name()
    # A readme-only agent (add_mcp_readme, no add_mcp_server) still publishes.
    has_tools = bool((_mcp_server or _mcp_readme_gate) and _mcp_tools)

    if has_tools:
        catalog_value = {
            'server': _mcp_server or {'description': ''},
            'tools': [
                {**tool, 'agent': agent_name}
                for tool in _mcp_tools
            ],
        }
        if _mcp_readme_gate:
            catalog_value['readme_gate'] = True
    else:
        catalog_value = {}

    try:
        collection('_mcp_tool_catalog').set(
            agent_name, state='registered', data=catalog_value,
        )
    except Exception as e:
        log_error(f"🔨 Failed to write MCP catalog row: {e}")
        # Fall through so per-tool subscriptions still register

    # LEGACY: pre-slug builds keyed this catalog row by AGENT_NAME (the display
    # name), producing invalid, mutable external tool names ("Meeting
    # Collector_ingest_transcript"). Drop that stale row on startup so the
    # orchestrator doesn't surface BOTH the old (invalid) and new (slug) tools.
    # Remove this cleanup once every deployed factory has restarted post-fix.
    legacy_key = config.AGENT_NAME
    if legacy_key and legacy_key != agent_name:
        try:
            collection('_mcp_tool_catalog').remove(legacy_key)
        except Exception as e:
            log_debug(f"🔨 MCP catalog legacy-row cleanup skipped: {e}")

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
