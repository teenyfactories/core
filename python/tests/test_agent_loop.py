"""Robustness tests for the tf-core agentic tool-calling loop (llm/agent.py).

Each test stubs the provider client (no network) and drives run_agent_loop through
a scripted sequence of AIMessages, asserting the loop's robustness guards:
  #1 max-turns preserves the model's last text
  #4 finish_reason == 'length' stops and preserves the partial
  #2 repeat identical tool errors → nudge, then terminate
  #5 default max_tokens is applied and overridable
  #8 a mid-loop exception surfaces partial (output, meta) on the with_meta path
"""
import importlib

import pytest

agent = importlib.import_module("teenyfactories.llm.agent")
builder_mod = importlib.import_module("teenyfactories.llm.builder")


class FakeAI:
    def __init__(self, content="", tool_calls=None, finish_reason="stop"):
        self.content = content
        self.tool_calls = tool_calls or []
        self.usage_metadata = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
                               "input_token_details": {"cache_read": 0, "cache_creation": 0}}
        self.response_metadata = {"finish_reason": finish_reason, "model_name": "demo"}


class ScriptedClient:
    """Returns the next scripted AIMessage each .invoke(); raise-on-turn support for #8."""
    def __init__(self, script, raise_at=None):
        self._script = script
        self._i = 0
        self._raise_at = raise_at
    def bind_tools(self, specs):
        return self
    def invoke(self, messages):
        if self._raise_at is not None and self._i == self._raise_at:
            raise RuntimeError("provider exploded")
        ai = self._script[min(self._i, len(self._script) - 1)]
        self._i += 1
        return ai


@pytest.fixture(autouse=True)
def _no_side_effects(monkeypatch):
    """Neutralize clearance + usage recording (both hit the orchestrator/DB)."""
    monkeypatch.setattr(agent, "_clearance_gate", lambda: None)
    monkeypatch.setattr(agent, "_log_turn_usage", lambda *a, **k: None)


def _builder(tools=None):
    b = builder_mod.llm().provider("openai").model("demo")
    for t in (tools or []):
        b.add_tool(t)
    return b


def _patch_client(monkeypatch, client, capture=None):
    def _factory(*a, **k):
        if capture is not None:
            capture.update(k)
        return client
    monkeypatch.setattr(agent.base, "get_llm_client", _factory)


# ── #5 default max_tokens applied + overridable ───────────────────────────────
def test_default_max_tokens_applied(monkeypatch):
    captured = {}
    _patch_client(monkeypatch, ScriptedClient([FakeAI(content="done")]), capture=captured)
    agent.run_agent_loop(_builder(), "go")
    assert captured["max_tokens"] == agent._DEFAULT_AGENT_MAX_TOKENS == 8192


def test_max_tokens_override_wins(monkeypatch):
    captured = {}
    _patch_client(monkeypatch, ScriptedClient([FakeAI(content="done")]), capture=captured)
    agent.run_agent_loop(_builder().max_tokens(2048), "go")
    assert captured["max_tokens"] == 2048


# ── #1 max-turns preserves last text ──────────────────────────────────────────
def test_max_turns_preserves_last_text(monkeypatch):
    def tool(_):
        return {"ok": True}
    # Every turn calls a tool + narrates → never a no-tool final answer → hits the cap.
    script = [FakeAI(content=f"thinking step {i}", tool_calls=[{"name": "tool", "args": {"i": i}, "id": f"t{i}"}],
                     finish_reason="tool_calls") for i in range(5)]
    _patch_client(monkeypatch, ScriptedClient(script))
    tool.__name__ = "tool"
    out, meta = agent.run_agent_loop(_builder([tool]).max_turns(3), "go", with_meta=True)
    assert meta["max_turns_reached"] is True
    assert meta["stop_reason"] == "max_turns"
    assert out == "thinking step 2"  # last text seen before the cap, NOT ""


# ── #4 finish_reason == 'length' ──────────────────────────────────────────────
def test_length_truncation_stops_and_preserves(monkeypatch):
    def tool(_):
        return {"ok": True}
    tool.__name__ = "tool"
    script = [
        FakeAI(content="partial answer that got cut off",
               tool_calls=[{"name": "tool", "args": {}, "id": "t0"}], finish_reason="length"),
        FakeAI(content="should never reach here"),
    ]
    _patch_client(monkeypatch, ScriptedClient(script))
    out, meta = agent.run_agent_loop(_builder([tool]).max_turns(10), "go", with_meta=True)
    assert meta["stop_reason"] == "length"
    assert out == "partial answer that got cut off"
    assert len(meta["tool_calls"]) == 0  # truncated tool call was NOT dispatched


# ── #2 repeat-error guardrail: nudge then terminate ───────────────────────────
def test_repeat_error_nudges_then_terminates(monkeypatch):
    def flaky(_):
        return {"error": "always fails"}
    flaky.__name__ = "flaky"
    # Model stubbornly calls flaky with identical args every turn.
    call = {"name": "flaky", "args": {"x": 1}, "id": "t"}
    script = [FakeAI(content=f"try {i}", tool_calls=[dict(call)], finish_reason="tool_calls") for i in range(10)]
    _patch_client(monkeypatch, ScriptedClient(script))
    out, meta = agent.run_agent_loop(_builder([flaky]).max_turns(10), "go", with_meta=True)
    assert meta["stop_reason"] == "repeat_error"
    assert meta["max_turns_reached"] is False  # terminated early, not at the cap
    assert out.startswith("try ")            # last narration preserved


def test_repeat_error_nudge_lets_model_recover(monkeypatch):
    calls = {"n": 0}
    def flaky(_):
        calls["n"] += 1
        return {"error": "fails"}
    flaky.__name__ = "flaky"
    bad = {"name": "flaky", "args": {"x": 1}, "id": "t"}
    # 3 identical failures (trip + nudge), then the model gives a clean final answer.
    script = [
        FakeAI(content="a", tool_calls=[dict(bad)], finish_reason="tool_calls"),
        FakeAI(content="b", tool_calls=[dict(bad)], finish_reason="tool_calls"),
        FakeAI(content="c", tool_calls=[dict(bad)], finish_reason="tool_calls"),
        FakeAI(content="recovered final answer"),
    ]
    _patch_client(monkeypatch, ScriptedClient(script))
    out, meta = agent.run_agent_loop(_builder([flaky]).max_turns(10), "go", with_meta=True)
    assert meta["stop_reason"] == "completed"
    assert out == "recovered final answer"


# ── #8 partial output/meta on mid-loop exception ──────────────────────────────
def test_exception_with_meta_returns_partial(monkeypatch):
    def tool(_):
        return {"ok": True}
    tool.__name__ = "tool"
    script = [
        FakeAI(content="made some progress", tool_calls=[{"name": "tool", "args": {}, "id": "t0"}],
               finish_reason="tool_calls"),
    ]
    _patch_client(monkeypatch, ScriptedClient(script, raise_at=1))  # explode on the 2nd invoke
    out, meta = agent.run_agent_loop(_builder([tool]).max_turns(10), "go", with_meta=True)
    assert meta["stop_reason"] == "error"
    assert meta["error"] and "exploded" in meta["error"]
    assert out == "made some progress"        # partial text preserved
    assert meta["usage"]["input_tokens"] > 0  # accumulated usage surfaced


def test_exception_without_meta_reraises(monkeypatch):
    _patch_client(monkeypatch, ScriptedClient([FakeAI()], raise_at=0))
    with pytest.raises(RuntimeError, match="exploded"):
        agent.run_agent_loop(_builder(), "go")  # plain path keeps the fail-loud contract
