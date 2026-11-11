"""Internal machine auth must not change outage policy or leak into external calls."""
from unittest.mock import Mock

import pytest
import requests

from teenyfactories import config, cost_clearance
from teenyfactories.internal_client import AgentAuthenticationError, headers


def test_headers_use_stable_authenticated_identity(monkeypatch):
    monkeypatch.setattr(config, "FACTORY_NAME", "canary")
    monkeypatch.setattr(config, "AGENT_SLUG", "probe")
    monkeypatch.setattr(config, "AUTH_AGENT_TOKEN", "private-test-token")
    assert headers() == {"X-Agent-Token": "private-test-token", "X-Factory-Name": "canary", "X-Agent-Slug": "probe"}


@pytest.mark.parametrize("status", [401, 403])
def test_clearance_auth_denial_blocks_call(monkeypatch, status):
    monkeypatch.setattr(cost_clearance.requests, "get", lambda *a, **k: Mock(status_code=status))
    monkeypatch.setattr(cost_clearance, "_feature_disabled", False)
    monkeypatch.setattr(cost_clearance, "_last_clear_ts", 0)
    with pytest.raises(AgentAuthenticationError, match="rejected"):
        cost_clearance.check_and_pause()
    assert cost_clearance._feature_disabled is False
    assert cost_clearance._last_clear_ts == 0


def test_clearance_outage_retains_fail_open(monkeypatch):
    def unavailable(*a, **k):
        raise requests.exceptions.Timeout()
    monkeypatch.setattr(cost_clearance.requests, "get", unavailable)
    monkeypatch.setattr(cost_clearance, "_warn_once", lambda *a: None)
    monkeypatch.setattr(cost_clearance, "_feature_disabled", False)
    monkeypatch.setattr(cost_clearance, "_last_clear_ts", 0)
    cost_clearance.check_and_pause()
    assert cost_clearance._last_clear_ts > 0


def test_clearance_attaches_credential_only_to_internal_service(monkeypatch):
    get = Mock(return_value=Mock(status_code=200, json=lambda: {"cleared": True}))
    monkeypatch.setattr(config, "INTERNAL_SERVICE_URL", "http://private-orchestrator:8998")
    monkeypatch.setattr(config, "AUTH_AGENT_TOKEN", "private-test-token")
    monkeypatch.setattr(cost_clearance.requests, "get", get)
    assert cost_clearance._fetch_clearance() == {"cleared": True}
    assert get.call_args.args[0] == "http://private-orchestrator:8998/llm-clearance"
    assert get.call_args.kwargs["headers"]["X-Agent-Token"] == "private-test-token"
