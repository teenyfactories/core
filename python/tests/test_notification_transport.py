"""Advisory wake parsing/coalescing stays bounded and independent of SQL."""
from threading import Lock

import pytest

from teenyfactories.notifications.orchestrator import OrchestratorNotifications, wake_events


def test_sse_chunks_crlf_comments_and_coalescing():
    data = b': heartbeat\r\n\r\nevent: wake\r\ndata: {}\r\n\r\nevent: other\ndata: {}\n\n'
    assert list(wake_events(bytes([byte]) for byte in data)) == [True]
    notification = object.__new__(OrchestratorNotifications)
    notification._lock = Lock()
    notification._pending = False
    for _ in range(1000):
        notification._signal()
    assert notification.consume_wake() is True
    assert notification.consume_wake() is False


@pytest.mark.parametrize('chunks', [[b'x' * 8193], [b'data: ' + b'x' * 4000 + b'\n'] * 3])
def test_unbounded_frames_are_rejected(chunks):
    with pytest.raises(ValueError, match='too large'):
        list(wake_events(chunks))
