"""Authentication shared by internal agent clients; never send it to external URLs."""

from . import config


class AgentAuthenticationError(PermissionError):
    """The reachable orchestrator rejected this agent's identity."""


def headers():
    return {
        "X-Agent-Token": config.AUTH_AGENT_TOKEN,
        "X-Factory-Name": config.FACTORY_NAME or "",
        "X-Agent-Slug": config.AGENT_SLUG or "",
    }


def check_auth(response):
    if response.status_code in (401, 403):
        raise AgentAuthenticationError("Orchestrator rejected the agent credential")
