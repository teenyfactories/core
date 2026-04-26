"""Redis message queue provider — placeholder for future work.

This module is intentionally a stub. A previous Redis provider existed but
was never wired up under the current `factory_data`-backed pub/sub model
(LISTEN/NOTIFY + state-driven rows). If Redis is revived as an alternative
transport, start here — the provider needs to:

1. Mirror the minimal surface area used by `message_queue.base`:
   connect(), listen(channel), poll_notifications(), fetch_item(...).
2. Coexist with the state/NOTIFY semantics: a catalog listener for catalog
   changes, and per-collection channels for state transitions.
3. Be selectable via a MESSAGE_QUEUE_PROVIDER env var dispatched in
   `message_queue.base._get_provider()`.

Remove this placeholder when the real implementation lands.
"""


class RedisProvider:
    """Placeholder. Raises on any method call."""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "Redis provider is not implemented. "
            "Use the default PostgreSQL provider."
        )
