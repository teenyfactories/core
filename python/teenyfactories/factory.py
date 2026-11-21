"""Explicit data/work scope; never changes the running agent's identity."""
import re


def validate_factory(name):
    if not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,29}', name):
        raise ValueError('Factory name must match [a-z][a-z0-9_-]{0,29}')
    if name == 'all':
        raise ValueError("All-factory access is not supported; select one named factory")
    return name


class Factory:
    """Select one owner for collection operations and claim-based subscriptions."""

    def __init__(self, name):
        self._name = validate_factory(name)

    def collection(self, name):
        from .collection import Collection
        return Collection(name, factory_name=self._name)

    def on_state(self, collection, state):
        from .message_queue.base import SubscriptionBuilder
        return SubscriptionBuilder(collection, state, factory_name=self._name)


def factory(name):
    """Return a named factory scope within this agent's configured installation."""
    return Factory(name)
