"""Unit tests for MCP tool-name validation (teenyfactories.mcp).

A tool name becomes a `_mcp_<name>` collection + Postgres NOTIFY channel and the
external `<agent>_<name>` tool id. Registration constrains it to the STRICTER
shape the `_mcp_<name>` collection CHECK enforces — ^[a-z0-9_]{1,35}$ — so a name
can never register here yet fail the DB CHECK at invoke time. Registration
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
    mcp._mcp_readmes.clear()
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
        'ingest_transcript',
        'a',
        'tool_123',
        'x' * 35,                     # exactly the 35-char cap
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
        'x' * 36,                     # over the 35-char cap
        'path/slash',                 # slash
        'Upper',                      # uppercase (now rejected)
        'has-hyphen',                 # hyphen (now rejected — fails the DB CHECK)
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
        assert '[a-z0-9_]{1,35}' in msg

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


# ---------------------------------------------------------------------------
# add_mcp_server: keyword-only signature; name= deprecated + ignored
# ---------------------------------------------------------------------------


class TestAddMcpServer:
    @pytest.fixture(autouse=True)
    def _reset_server(self, monkeypatch):
        """Isolate _mcp_server + capture the deprecation info-log per test."""
        monkeypatch.setattr(mcp, '_mcp_server', None)
        infos = []
        monkeypatch.setattr(mcp, 'log_info', lambda m: infos.append(m))
        monkeypatch.setattr(mcp, 'log_debug', lambda m: None)
        return infos

    def test_description_only_sets_server_without_name(self, _reset_server):
        mcp.add_mcp_server(description='Query and analyse spend')
        assert mcp._mcp_server == {'description': 'Query and analyse spend'}
        assert 'name' not in mcp._mcp_server        # name field is gone
        assert _reset_server == []                  # no deprecation log

    def test_bare_call_is_allowed_description_defaults_empty(self, _reset_server):
        mcp.add_mcp_server()                        # the publish switch, no description
        assert mcp._mcp_server == {'description': ''}
        assert _reset_server == []

    def test_name_kwarg_is_deprecated_and_ignored(self, _reset_server):
        mcp.add_mcp_server(description='desc', name='legacy-name')
        assert mcp._mcp_server == {'description': 'desc'}   # name ignored, not stored
        assert len(_reset_server) == 1                       # deprecation logged once
        assert 'deprecated' in _reset_server[0].lower()
        assert 'legacy-name' in _reset_server[0]

    def test_description_is_keyword_only_positional_errors(self, _reset_server):
        # Both args are keyword-only: a bare positional description, and the old
        # positional (name, desc) form, must both fail loudly rather than bind.
        with pytest.raises(TypeError):
            mcp.add_mcp_server('desc')                       # type: ignore[misc]
        with pytest.raises(TypeError):
            mcp.add_mcp_server('legacy-name', 'desc')        # type: ignore[misc]


# ---------------------------------------------------------------------------
# add_mcp_readme: registers the read_me_first tool + sets the gate flag
# ---------------------------------------------------------------------------


class TestAddMcpReadme:
    @pytest.fixture(autouse=True)
    def _reset_gate(self, monkeypatch):
        monkeypatch.setattr(mcp, '_mcp_readme_gate', False)

    def test_registers_read_me_first_tool_and_sets_gate(self):
        mcp.add_mcp_readme('Filing conventions: search before create.')
        assert [t['name'] for t in mcp._mcp_tools] == ['read_me_first']
        assert mcp._mcp_readme_gate is True

    def test_handler_returns_the_body_verbatim(self):
        body = 'How to use these tools: always search first.'
        mcp.add_mcp_readme(body)
        assert mcp._mcp_handlers['read_me_first']({}) == body   # params ignored, body returned

    def test_fixed_description_carries_read_first_nudge(self):
        mcp.add_mcp_readme('body')
        tool = next(t for t in mcp._mcp_tools if t['name'] == 'read_me_first')
        assert 'read' in tool['description'].lower()
        assert tool['annotations']['readOnlyHint'] is True

    def test_idempotent_second_call_replaces_body_no_duplicate(self):
        mcp.add_mcp_readme('first')
        mcp.add_mcp_readme('second')
        readmes = [t for t in mcp._mcp_tools if t['name'] == 'read_me_first']
        assert len(readmes) == 1                                # not double-registered
        assert mcp._mcp_handlers['read_me_first']({}) == 'second'

    def test_default_body_carries_readme_bodies_all_surfaces(self):
        mcp.add_mcp_readme('all')
        tool = next(t for t in mcp._mcp_tools if t['name'] == 'read_me_first')
        assert tool['readme_bodies'] == [{'audience': None, 'body': 'all'}]
        assert 'hidden_from' not in tool                        # visible on every surface


class TestReadmeAudience:
    @pytest.fixture(autouse=True)
    def _reset_gate(self, monkeypatch):
        monkeypatch.setattr(mcp, '_mcp_readme_gate', False)

    def _readme_tool(self):
        return next(t for t in mcp._mcp_tools if t['name'] == 'read_me_first')

    def test_per_audience_bodies_coexist_and_serve_by_surface(self):
        mcp.add_mcp_readme('external product how-to').audience(['external'])
        mcp.add_mcp_readme('internal loop guide').audience(['internal'])
        deduped = mcp._deduped_readmes()
        assert mcp._select_readme_body('external', deduped) == 'external product how-to'
        assert mcp._select_readme_body('foreman', deduped) == 'internal loop guide'
        assert mcp._select_readme_body('agent_loop', deduped) == 'internal loop guide'
        # union of {external} ∪ {foreman,agent_loop} = all ⇒ no hidden_from
        assert 'hidden_from' not in self._readme_tool()
        assert mcp._mcp_readme_gate is True                     # internal includes foreman

    def test_specific_audience_wins_over_default(self):
        mcp.add_mcp_readme('default for all')
        mcp.add_mcp_readme('external-specific').audience(['external'])
        deduped = mcp._deduped_readmes()
        assert mcp._select_readme_body('external', deduped) == 'external-specific'
        assert mcp._select_readme_body('foreman', deduped) == 'default for all'

    def test_external_only_readme_hides_other_surfaces_and_does_not_gate_foreman(self):
        mcp.add_mcp_readme('public only').audience(['external'])
        tool = self._readme_tool()
        assert tool['hidden_from'] == ['agent_loop', 'foreman']
        assert mcp._mcp_readme_gate is False                    # foreman never sees it → no deadlock
        # round-trip handler (agent_loop) still returns *something* safe
        assert mcp._mcp_handlers['read_me_first']({}) == 'public only'

    def test_same_audience_reregistration_replaces_body(self):
        mcp.add_mcp_readme('v1').audience(['external'])
        mcp.add_mcp_readme('v2').audience(['external'])
        tool = self._readme_tool()
        assert tool['readme_bodies'] == [{'audience': ['external'], 'body': 'v2'}]

    def test_handler_returns_agent_loop_body_when_present(self):
        mcp.add_mcp_readme('for external').audience(['external'])
        mcp.add_mcp_readme('for the loop').audience(['agent_loop'])
        assert mcp._mcp_handlers['read_me_first']({}) == 'for the loop'


# ---------------------------------------------------------------------------
# Tool audience scoping: .audience([...]) whitelist → hidden_from denylist
# ---------------------------------------------------------------------------


class TestToolAudience:
    def _tool(self):
        return mcp._mcp_tools[-1]

    def test_default_visible_no_hidden_from_field(self):
        mcp.add_mcp_tool('t_default', 'd').do(lambda p: None)
        assert 'hidden_from' not in self._tool()               # omitted ⇒ visible everywhere

    def test_audience_all_surfaces_has_no_hidden_from(self):
        mcp.add_mcp_tool('t_all', 'd') \
            .audience(['external', 'foreman', 'agent_loop']).do(lambda p: None)
        assert 'hidden_from' not in self._tool()               # whole complement empty

    def test_audience_external_only_hides_the_rest(self):
        mcp.add_mcp_tool('t_ext', 'd').audience(['external']).do(lambda p: None)
        assert self._tool()['hidden_from'] == ['agent_loop', 'foreman']

    def test_audience_foreman_only_hides_the_rest(self):
        mcp.add_mcp_tool('t_fore', 'd').audience(['foreman']).do(lambda p: None)
        assert self._tool()['hidden_from'] == ['agent_loop', 'external']

    def test_audience_internal_alias_expands_to_foreman_and_loop(self):
        mcp.add_mcp_tool('t_int', 'd').audience(['internal']).do(lambda p: None)
        assert self._tool()['hidden_from'] == ['external']     # internal = foreman + agent_loop

    def test_audience_accepts_bare_string(self):
        mcp.add_mcp_tool('t_str', 'd').audience('external').do(lambda p: None)
        assert self._tool()['hidden_from'] == ['agent_loop', 'foreman']

    def test_empty_audience_is_pipeline_only_sorted(self):
        mcp.add_mcp_tool('t_pipe', 'd').audience([]).do(lambda p: None)
        assert self._tool()['hidden_from'] == ['agent_loop', 'external', 'foreman']

    def test_unknown_surface_is_ignored(self):
        mcp.add_mcp_tool('t_bad', 'd').audience(['external', 'bogus']).do(lambda p: None)
        assert self._tool()['hidden_from'] == ['agent_loop', 'foreman']  # bogus dropped

    def test_audience_returns_builder_for_chaining(self):
        b = mcp.add_mcp_tool('t_chain', 'd')
        assert b.audience(['external']) is b
