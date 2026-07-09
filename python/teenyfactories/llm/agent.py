"""Agentic tool-calling loop for ``tf.llm().run_agent_loop*`` (Phase 2 + 4).

Native provider tool-calling (``bind_tools``) over the existing MCP registry,
with a SERIAL dispatch loop (no threads / asyncio / multiprocessing — matches
tf's single-threaded run_pending model), arg-validation + error-feedback to the
model, per-tool-result capping + bounded history, and API-error retry/backoff.

Tools are sourced either LOCALLY (this agent's own ``tf.add_mcp_tool`` handlers)
or OVER THE WIRE (another agent in this factory — the librarian↔Wiki-Ops split):
dispatch writes ``_mcp_<tool>.request`` and bounded-polls ``.response``.

The loop emits no custom outcome enum — it returns the provider's verbatim
``finish_reason`` plus a ``max_turns_reached`` bool; factories map those to their
own labels.
"""

import json
import time
import uuid

from teenyfactories.logging import log_debug, log_error, log_warn
from . import base
from . import caching

# Bound the message history + per-tool-result size so a long run can't blow the
# context window (replaces the librarian's hand-rolled scratchpad trim). Hysteresis
# watermarks: trim only when HIGH is exceeded, down to LOW, then hold stable — so the
# prefix stays byte-identical across turns and the rolling tail cache keeps hitting.
_HISTORY_HIGH_WATERMARK = 60
_HISTORY_LOW_WATERMARK = 40
_TOOL_RESULT_CAP_CHARS = 12000
_WIRE_TIMEOUT_S = 90.0  # over-wire tools may be LLM-backed (e.g. a broker RFI) — allow for a model round-trip under load
_API_RETRIES = 3


# ── tool sourcing + conversion ────────────────────────────────────────────────


def _to_spec(tool: dict, hide_keys=None) -> dict:
    """MCP tool dict {name, description, inputSchema} → OpenAI function tool spec
    (LangChain's bind_tools normalises this per provider). ``hide_keys`` removes
    dispatch-time-injected params from the schema the model sees (it never supplies
    them — they're forced at dispatch)."""
    params = tool.get("inputSchema") or {"type": "object", "properties": {}}
    if hide_keys:
        params = dict(params)
        params["properties"] = {k: v for k, v in (params.get("properties") or {}).items() if k not in hide_keys}
        if params.get("required"):
            params["required"] = [r for r in params["required"] if r not in hide_keys]
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": params,
        },
    }


def _injected_keys_for(builder, tool_name: str) -> set:
    """Keys forced at dispatch for this tool (so they're hidden from the model)."""
    keys = set()
    for mapping, only in builder._arg_injections:
        if only is None or tool_name in only:
            keys.update(mapping.keys())
    return keys


def _gather_tools(builder):
    """Return (specs, dispatch, schemas): bind_tools specs, name→callable dispatch
    map, and name→inputSchema for arg validation."""
    specs, dispatch, schemas = [], {}, {}

    def _add(tool, handler):
        specs.append(_to_spec(tool, hide_keys=_injected_keys_for(builder, tool["name"])))
        dispatch[tool["name"]] = handler
        schemas[tool["name"]] = tool.get("inputSchema") or {}

    for kind, name in builder._tool_sources:
        if kind == "self":
            from teenyfactories import mcp

            for tool in mcp._mcp_tools:
                _add(tool, mcp._mcp_handlers.get(tool["name"]))
        elif kind == "agent":
            for tool in _agent_catalog_tools(name):
                _add(tool, _wire_dispatcher(tool["name"], name))

    for fn_or_name in builder._extra_tools:
        from teenyfactories import mcp

        if isinstance(fn_or_name, str):
            tool = next((t for t in mcp._mcp_tools if t["name"] == fn_or_name), None)
            if tool:
                _add(tool, mcp._mcp_handlers.get(fn_or_name))
        else:
            nm = getattr(fn_or_name, "__name__", "tool")
            _add(
                {
                    "name": nm,
                    "description": (fn_or_name.__doc__ or ""),
                    "inputSchema": {"type": "object", "properties": {}},
                },
                fn_or_name,
            )

    return specs, dispatch, schemas


def _agent_catalog_tools(agent_name: str) -> list:
    """Read another agent's published tool specs from _mcp_tool_catalog."""
    from teenyfactories.collection import collection

    row = collection("_mcp_tool_catalog").get(agent_name)
    if not row:
        log_warn(f"🔧 add_tools_from_agent('{agent_name}'): no catalog row — no tools bound")
        return []
    return ((row.get("data") or {}).get("tools")) or []


def _wire_dispatcher(tool_name: str, agent_name: str):
    """Dispatch a tool OVER THE WIRE to another agent (the librarian's
    _call_wiki_tool pattern, moved into the framework): write _mcp_<tool>.request,
    bounded-poll .response. Returns an {'error': …} dict on timeout (the loop
    feeds that back to the model — never crashes)."""

    def _dispatch(params):
        from teenyfactories.collection import collection

        key = "call-" + uuid.uuid4().hex[:12]
        coll = collection(f"_mcp_{tool_name}")
        try:
            coll.set(key, state="request", data={"params": params, "agent": agent_name})
        except Exception as e:
            return {"error": f"could not queue tool call: {e}"}
        waited = 0.0
        while waited < _WIRE_TIMEOUT_S:
            try:
                time.sleep(0.25)
            except Exception:
                pass
            waited += 0.25
            row = coll.get(key)
            d = (row or {}).get("data") or {}
            if "result" in d or "error" in d:
                try:
                    coll.remove(key)
                except Exception:
                    pass
                return d["result"] if "result" in d else {"error": d.get("error")}
        try:
            coll.remove(key)
        except Exception:
            pass
        return {"error": f"timeout: tool '{tool_name}' did not respond within {_WIRE_TIMEOUT_S:.0f}s"}

    return _dispatch


# ── dispatch (serial, validated, error-fed-back) ──────────────────────────────


def _validate_args(args: dict, schema: dict):
    """Best-effort: ensure required props are present + types roughly match.
    Returns an error string, or None if OK. Uses jsonschema if available."""
    try:
        import jsonschema

        jsonschema.validate(instance=args, schema=schema)
        return None
    except ImportError:
        required = (schema or {}).get("required") or []
        missing = [r for r in required if r not in (args or {})]
        return f"missing required argument(s): {missing}" if missing else None
    except Exception as ve:  # jsonschema.ValidationError
        return f"invalid arguments: {getattr(ve, 'message', str(ve))}"


def _dispatch_tool(tc, dispatch, schemas, builder):
    """Run one tool call. Validate args → run handler → return the result (or an
    {'error': …} dict). Never raises (errors are fed back to the model)."""
    name = tc.get("name")
    args = dict(tc.get("args") or {})

    # Dispatch-time arg injection (e.g. the librarian's source_meeting on mutating
    # tools — kept OUT of the schema the model sees, forced here).
    for mapping, only in builder._arg_injections:
        if only is None or name in only:
            args = {**args, **mapping}

    handler = dispatch.get(name)
    if handler is None:
        return {"error": f"unknown tool '{name}'"}

    err = _validate_args(args, schemas.get(name) or {})
    if err is not None:
        log_debug(f"🔧 tool '{name}' arg validation failed → fed back to model: {err}")
        return {"error": err}

    try:
        return handler(args)
    except Exception as e:
        log_debug(f"🔧 tool '{name}' raised → fed back to model: {e}")
        return {"error": str(e)}


# ── history management ─────────────────────────────────────────────────────────


def _cap_tool_result(result) -> str:
    """JSON-encode a tool result, capping size with a completeness marker so the
    model knows when it's truncated (preserves the re-fetch-prevention signal)."""
    try:
        # sort_keys so tool-result key order can't shift the cached prefix.
        s = result if isinstance(result, str) else json.dumps(result, sort_keys=True)
    except Exception:
        s = str(result)
    if len(s) > _TOOL_RESULT_CAP_CHARS:
        return s[:_TOOL_RESULT_CAP_CHARS] + "\n[TRUNCATED — result capped; this is PARTIAL, not the full output]"
    return s


def _trim_history(messages):
    """Keep the leading system message(s) + the most recent window so the context
    can't grow unbounded over a long run. Hysteresis: only trim once the HIGH
    watermark is exceeded, chunk back to LOW, then hold stable — so the prefix stays
    byte-identical between the rare trims and the rolling tail cache keeps hitting
    (trimming every turn would shift the prefix and defeat it)."""
    if len(messages) <= _HISTORY_HIGH_WATERMARK:
        return messages
    from langchain_core.messages import SystemMessage, ToolMessage

    head = [m for m in messages[:2] if isinstance(m, SystemMessage)]
    window = messages[-(_HISTORY_LOW_WATERMARK - len(head)) :]
    # A window starting on a ToolMessage is an orphaned tool_result — its tool_use
    # AIMessage got trimmed off. Anthropic rejects that (400). Drop leading orphans.
    while window and isinstance(window[0], ToolMessage):
        window = window[1:]
    return head + window


# ── usage folding ──────────────────────────────────────────────────────────────


def _fold_usage(agg: dict, raw: dict):
    um = (raw or {}).get("usage_metadata") or {}
    agg["input_tokens"] += um.get("input_tokens") or 0
    agg["output_tokens"] += um.get("output_tokens") or 0
    agg["total_tokens"] += um.get("total_tokens") or 0
    itd = um.get("input_token_details") or {}
    agg["input_token_details"]["cache_read"] += itd.get("cache_read") or 0
    agg["input_token_details"]["cache_creation"] += itd.get("cache_creation") or 0
    cost = ((raw.get("response_metadata") or {}).get("token_usage") or {}).get("cost")
    if cost is not None:
        agg["cost"] = (agg["cost"] or 0) + cost
    agg["turns"] += 1


# ── provider invoke with retry/backoff ────────────────────────────────────────


def _invoke_with_retry(bound, messages):
    """Invoke the bound client with exponential backoff on transient API errors
    (429/5xx/rate-limit). Respects shutting_down() so SIGTERM doesn't hang."""
    delay = 1.0
    last_err = None
    for attempt in range(_API_RETRIES):
        try:
            return bound.invoke(messages)
        except Exception as e:
            last_err = e
            msg = str(e).lower()
            transient = any(
                t in msg
                for t in ("429", "rate limit", "rate_limit", "500", "502", "503", "504", "overloaded", "timeout")
            )
            if not transient or attempt == _API_RETRIES - 1:
                raise
            log_debug(f"💬 transient API error (attempt {attempt+1}/{_API_RETRIES}, retrying in {delay:.0f}s): {e}")
            if _shutting_down():
                raise
            try:
                time.sleep(delay)
            except Exception:
                pass
            delay *= 2
    raise last_err if last_err else Exception("invoke failed")


def _shutting_down() -> bool:
    try:
        from teenyfactories.lifecycle import shutting_down

        return bool(shutting_down())
    except Exception:
        return False


# ── the loop ────────────────────────────────────────────────────────────────────


def run_agent_loop(builder, task, with_meta=False):
    """Native-tool-calling ReAct loop. Returns the final output, or (output, meta)
    when with_meta. meta carries usage + turns + tool_calls + finish_reason +
    max_turns_reached. EAGER — the tools mutate state as this runs."""
    from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage

    specs, dispatch, schemas = _gather_tools(builder)
    used_provider = base._resolve_provider(builder._provider)
    used_model = base._get_model_name(builder._provider, model=builder._model)
    request_id = str(uuid.uuid4())

    client = base.get_llm_client(
        builder._provider,
        model=builder._model,
        temperature=builder._temperature,
        max_tokens=builder._max_tokens,
        extra_body=builder._extra_body or None,
    )
    if not hasattr(client, "bind_tools"):
        raise NotImplementedError(
            f"tf.llm().run_agent_loop requires native tool-calling, which {used_provider}/{used_model} "
            f"does not support. Use .ask() / .ask_with_meta(), or switch provider."
        )

    # System prefix is cacheable; mark it where the provider supports it.
    system_msg = (
        caching.cache_system_message(SystemMessage(content=builder._system), used_provider) if builder._system else None
    )
    bound = caching.bind_tools_cached(client, specs, used_provider) if specs else client

    messages = ([system_msg] if system_msg else []) + [HumanMessage(content=task)]

    turns, tool_calls_all = [], []
    usage_agg = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "input_token_details": {"cache_read": 0, "cache_creation": 0},
        "cost": None,
        "turns": 0,
    }
    finish_reason = None
    max_turns_reached = False
    output = ""
    # One client, reused every turn — a caller's stable extra_body (e.g. OpenRouter
    # provider.order) rides every request unchanged, best-effort consistent routing
    # so the upstream prefix-cache bites. tf does not auto-pin (see caching.py).

    for turn_idx in range(builder._max_turns):
        # Rolling cache breakpoint on the tail (Anthropic) — invoke on a marked copy
        # so the persistent `messages` list stays marker-free.
        invoke_msgs = caching.mark_cache_tail(messages, used_provider)
        t0 = time.time()
        ai = _invoke_with_retry(bound, invoke_msgs)
        turn_ms = int((time.time() - t0) * 1000)

        raw = base._extract_token_info(ai).get("raw") or {}
        _fold_usage(usage_agg, raw)
        finish_reason = (raw.get("response_metadata") or {}).get("finish_reason")
        _log_turn_usage(used_provider, used_model, raw, turn_ms, request_id)

        tool_names = [tc.get("name") for tc in (ai.tool_calls or [])]
        turn_info = {"turn": turn_idx, "tool_calls": tool_names, "usage": raw.get("usage_metadata")}
        turns.append(turn_info)
        if builder._on_turn:
            try:
                builder._on_turn(turn_info)
            except Exception as e:
                log_warn(f"💬 on_turn callback raised: {e}")

        if not ai.tool_calls:
            output = ai.content or ""
            break

        messages.append(ai)
        for tc in ai.tool_calls:  # SERIAL — no parallelism
            result = _dispatch_tool(tc, dispatch, schemas, builder)
            tool_calls_all.append({"name": tc.get("name"), "args": tc.get("args"), "result": result})
            messages.append(ToolMessage(content=_cap_tool_result(result), tool_call_id=tc.get("id")))
        messages = _trim_history(messages)
    else:
        max_turns_reached = True
        log_debug(f"💬 run_agent_loop hit max_turns ({builder._max_turns})")

    meta = base._meta_from_raw({}, used_provider, used_model, None)
    meta.update(
        {
            "usage": usage_agg,
            "turns": turns,
            "tool_calls": tool_calls_all,
            "finish_reason": finish_reason,
            "max_turns_reached": max_turns_reached,
            "cost": usage_agg.get("cost"),
        }
    )
    return (output, meta) if with_meta else output


def _log_turn_usage(provider, model, raw, latency_ms, request_id):
    """Record one turn's usage under the run's shared request_id."""
    try:
        from teenyfactories.usage_recorder import log_usage

        log_usage(
            call_kind="llm",
            provider=provider,
            model=model,
            raw=raw,
            latency_ms=latency_ms,
            request_id=request_id,
            chat_id=None,
        )
    except Exception as e:
        log_warn(f"💬 turn usage_recorder unavailable: {e}")
