"""Unit tests for MCP tool-name validation (teenyfactories.mcp).

A tool name becomes a `_mcp_<name>` collection + Postgres NOTIFY channel and the
external `<agent>_<name>` tool id (must match ^[A-Za-z0-9_-]{1,64}$). Registration
must REJECT a name that doesn't match: log an ERROR to factory_logs and NOT land
the tool in the catalog. These are pure-function tests — `mcp.log_error` is
monkeypatched to capture messages, and the module registry is reset per-test, so
no DB is needed.
"""

import pytest

import teenyfactories.mcp as mcp


@pytest.fixture(autouse=True)
def _reset_registry(monkeypatch):
    """Isolate the module-level registry + capture error logs for each test."""
    mcp._mcp_tools.clear()
    mcp._mcp_handlers.clear()
    errors = []
    monkeypatch.setattr(mcp, 'log_error', lambda m: errors.append(m))
    # Silence the debug line the happy path emits.
    monkeypatch.setattr(mcp, 'log_debug', lambda m: None)
    return errors


def _register(name):
    return mcp.add_mcp_tool(name, 'desc').do(lambda params: None)


# ---------------------------------------------------------------------------
# Valid names register
# ---------------------------------------------------------------------------

class TestValidNames:
    @pytest.mark.parametrize('name', [
        'query_spend',
        'ingest-transcript',
        'A',
        'Tool_123',
        'x' * 64,                     # exactly the 64-char cap
    ])
    def test_valid_name_registers(self, name, _reset_registry):
        _register(name)
        assert [t['name'] for t in mcp._mcp_tools] == [name]
        assert name in mcp._mcp_handlers
        assert _reset_registry == []   # no error logged

    def test_do_returns_handler_on_success(self):
        def handler(params):
            return None
        assert mcp.add_mcp_tool('ok_tool', 'd').do(handler) is handler


# ---------------------------------------------------------------------------
# Invalid names are rejected (logged + not registered)
# ---------------------------------------------------------------------------

class TestInvalidNames:
    @pytest.mark.parametrize('name', [
        'has space',                  # space
        'has.dot',                    # dot
        '',                           # empty
        'x' * 65,                     # over the 64-char cap
        'path/slash',                 # slash
    ])
    def test_invalid_name_rejected(self, name, _reset_registry):
        _register(name)
        # Not registered anywhere.
        assert mcp._mcp_tools == []
        assert mcp._mcp_handlers == {}
        # Exactly one ERROR row logged, naming the offender + the pattern.
        assert len(_reset_registry) == 1
        msg = _reset_registry[0]
        assert repr(name) in msg
        assert '[a-zA-Z0-9_-]{1,64}' in msg

    def test_do_still_returns_handler_on_rejection(self):
        def handler(params):
            return None
        # Skip-with-error-log convention: agent keeps running, .do() returns
        # the handler unchanged so decorator usage doesn't explode.
        assert mcp.add_mcp_tool('bad name', 'd').do(handler) is handler

    def test_non_string_name_rejected_not_crashed(self, _reset_registry):
        _register(12345)               # type: ignore[arg-type]
        assert mcp._mcp_tools == []
        assert len(_reset_registry) == 1
