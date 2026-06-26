"""Fluent ``tf.llm()`` builder — config-first, eager terminals.

    tf.llm()
      .model(name) .provider(name) .temperature(t) .max_tokens(n)
      .system(text) .with_structured_output(Model)
      .ask(prompt, inputs)            -> output
      .ask_with_meta(prompt, inputs)  -> (output, meta)
      # agentic (Phase 2):
      .add_tools_from_self() .add_tools_from_agent(name) .add_tool(fn)
      .max_turns(n) .on_turn(cb)
      .run_agent_loop(task)           -> output
      .run_agent_loop_with_meta(task) -> (output, meta)

Single-shot (.ask*) reuses the same helpers as the LEGACY ``call_llm`` (base.py).
Structured output prefers the provider's NATIVE enforcement
(``with_structured_output``) and falls back to the LEGACY ``PydanticOutputParser``
path when a provider/model doesn't enforce it — logging the degrade so OpenRouter
soft-failures (accepts the param, upstream ignores it) are visible.

`meta` is a plain dict (see base._meta_from_raw): {provider, model, cost,
finish_reason, latency_ms, usage, raw} — all fields best-effort/nullable.
"""

import time
from typing import Optional, Type

from teenyfactories.logging import log_debug, log_error, log_warn
from . import base


def llm() -> "LLMBuilder":
    """Entry point for the fluent LLM builder. See module docstring."""
    return LLMBuilder()


class LLMBuilder:
    def __init__(self):
        self._provider: Optional[str] = None
        self._model: Optional[str] = None
        self._temperature: Optional[float] = None
        self._max_tokens: Optional[int] = None
        self._system: Optional[str] = None
        self._structured: Optional[Type] = None
        # agentic config (Phase 2)
        self._tool_sources: list = []
        self._extra_tools: list = []
        self._max_turns: int = 50
        self._on_turn = None
        # dispatch-time arg injections: list of (mapping, only_tools_or_None)
        self._arg_injections: list = []

    # ── config links ─────────────────────────────────────────────────────────
    def model(self, name: str) -> "LLMBuilder":
        self._model = name
        return self

    def provider(self, name: str) -> "LLMBuilder":
        self._provider = name
        return self

    def temperature(self, t: float) -> "LLMBuilder":
        self._temperature = t
        return self

    def max_tokens(self, n: int) -> "LLMBuilder":
        self._max_tokens = n
        return self

    def system(self, text: str) -> "LLMBuilder":
        self._system = text
        return self

    def with_structured_output(self, model: Type) -> "LLMBuilder":
        self._structured = model
        return self

    # ── agentic config (Phase 2 — recorded now, consumed by the loop later) ───
    def add_tools_from_self(self) -> "LLMBuilder":
        self._tool_sources.append(("self", None))
        return self

    def add_tools_from_agent(self, name: str) -> "LLMBuilder":
        self._tool_sources.append(("agent", name))
        return self

    def add_tool(self, fn_or_name) -> "LLMBuilder":
        self._extra_tools.append(fn_or_name)
        return self

    def max_turns(self, n: int) -> "LLMBuilder":
        self._max_turns = n
        return self

    def on_turn(self, cb) -> "LLMBuilder":
        self._on_turn = cb
        return self

    def inject_tool_args(self, mapping: dict, tools=None) -> "LLMBuilder":
        """Force these args into tool calls at DISPATCH time (the model never sees
        them — keep them out of the inputSchema). ``tools`` limits which tools get
        the injection (None = all). Used e.g. for the librarian's ``source_meeting``
        provenance on mutating wiki tools."""
        self._arg_injections.append((dict(mapping), set(tools) if tools else None))
        return self

    # ── single-shot terminals (eager) ────────────────────────────────────────
    def ask(self, prompt, inputs=None):
        return self._ask(prompt, inputs)[0]

    def ask_with_meta(self, prompt, inputs=None):
        return self._ask(prompt, inputs)

    # ── agentic terminals (eager) ─────────────────────────────────────────────
    def run_agent_loop(self, task):
        from . import agent

        return agent.run_agent_loop(self, task, with_meta=False)

    def run_agent_loop_with_meta(self, task):
        from . import agent

        return agent.run_agent_loop(self, task, with_meta=True)

    # ── single-shot implementation ───────────────────────────────────────────
    def _ask(self, prompt, inputs):
        """Run a single-shot call; return (output, meta). Mirrors call_llm's
        orchestration (clearance gate → client → invoke → usage in finally),
        reusing base.py helpers verbatim."""
        inputs = dict(inputs or {})
        start = time.time()
        used_provider = base._resolve_provider(self._provider)
        used_model = base._get_model_name(self._provider, model=self._model)
        token_info: dict = {}
        success = False
        error_message = None
        output = None

        try:
            _clearance_gate()
            client = base.get_llm_client(
                self._provider,
                model=self._model,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
            )
            template = self._build_template(prompt)

            if self._structured is not None:
                output, raw_result = self._structured_invoke(client, template, inputs)
            else:
                raw_result, output = base._invoke_chain(template, client, inputs)

            token_info = base._extract_token_info(raw_result)
            success = True
        except Exception as e:
            error_message = str(e)
            log_error(f"💬 tf.llm().ask failed: {error_message}")
        finally:
            duration_ms = int((time.time() - start) * 1000)
            base._record_call_usage(
                provider=used_provider,
                model=used_model,
                token_info=token_info,
                duration_ms=duration_ms,
                prompt_template=prompt,
                prompt_inputs=inputs,
            )

        if not success:
            raise Exception(error_message or "tf.llm().ask: produced no result")
        meta = base._meta_from_raw(token_info.get("raw"), used_provider, used_model, int((time.time() - start) * 1000))
        return output, meta

    def _build_template(self, prompt):
        """Compose the prompt. With .system(), wrap as a ChatPromptTemplate where
        the system block is a LITERAL message (so braces in SYSTEM aren't parsed
        as template vars) and the human part stays templated. Without .system(),
        the prompt template is used directly (identical to call_llm)."""
        from langchain_core.prompts import PromptTemplate, HumanMessagePromptTemplate, ChatPromptTemplate
        from langchain_core.messages import SystemMessage

        if isinstance(prompt, str):
            prompt = PromptTemplate.from_template(prompt)
        if self._system is None:
            return prompt
        human = HumanMessagePromptTemplate.from_template(getattr(prompt, "template", str(prompt)))
        return ChatPromptTemplate.from_messages([SystemMessage(content=self._system), human])

    def _structured_invoke(self, client, template, inputs):
        """Native structured output first (provider-enforced via
        with_structured_output(include_raw=True)); fall back to the LEGACY
        PydanticOutputParser path if the provider/model doesn't enforce it.
        Returns (parsed_model, raw_message)."""
        Model = self._structured
        try:
            structured = client.with_structured_output(Model, include_raw=True)
            result = (template | structured).invoke(inputs)
            parsed = result.get("parsed") if isinstance(result, dict) else None
            raw_msg = result.get("raw") if isinstance(result, dict) else None
            if parsed is not None and not (isinstance(result, dict) and result.get("parsing_error")):
                return parsed, raw_msg
            log_debug(
                f"💬 native structured output not enforced for {Model.__name__} "
                f"({base._resolve_provider(self._provider)}/{base._get_model_name(self._provider, model=self._model)}) "
                f"— falling back to PydanticOutputParser"
            )
        except Exception as e:
            log_debug(
                f"💬 native structured output unavailable for {Model.__name__} "
                f"({e}) — falling back to PydanticOutputParser"
            )

        # LEGACY fallback path (same mechanism as call_llm): inject format
        # instructions, invoke, clean, parse.
        pt, parser = base._prepare_prompt(template, inputs, Model)
        raw_msg, text = base._invoke_chain(pt, client, inputs)
        text = base.clean_json_response(text)
        parsed = base._parse_response(text, parser, Model)
        return parsed, raw_msg


def _clearance_gate():
    """Spend-limit clearance — the orchestrator gates the call before the
    provider request. Fails OPEN (same as call_llm)."""
    try:
        from teenyfactories.cost_clearance import check_and_pause

        check_and_pause()
    except Exception as clearance_err:
        log_warn(f"💬 LLM clearance check unavailable (proceeding): {clearance_err}")
