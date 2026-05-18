"""Redis message queue provider — placeholder for future work.

This module is intentionally a stub. A previous Redis provider existed but
was never wired up under the current `factory_data`-backed pub/sub model
(LISTEN/NOTIFY + state-driven rows). If Redis is revived as an alternative
transport, start here — the provider needs to:

1. Mirror the minimal surface area used by `message_queue.base`:
   connect(), listen(channel), poll_notifications(),
   fetch_rows(collection, state),
   fetch_due_rows(collection, state, delay_seconds).
2. Coexist with the pipeline-poll model: a single advisory wake channel
   (equivalent of `tf_data_changed`) used only to trigger a poll — the
   state itself is the queue; dispatch is poll-based, not push.
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
