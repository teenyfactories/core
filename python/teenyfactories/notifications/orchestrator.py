"""Bounded SSE wake reception. The network thread never touches the datastore/logger."""
import atexit
import random
from threading import Event, Lock, Thread

import requests

from .. import config
from ..internal_client import headers


def wake_events(chunks):
    """Parse only the SSE wake signal, with bounded line/frame storage."""
    buffer = b''
    event = ''
    size = 0
    for chunk in chunks:
        buffer += chunk
        while b'\n' in buffer:
            line, buffer = buffer.split(b'\n', 1)
            line = line.rstrip(b'\r')
            size += len(line) + 1
            if size > 8192:
                raise ValueError('Wake frame too large')
            if not line:
                if event == 'wake':
                    yield True
                event, size = '', 0
            elif line.startswith(b'event:'):
                event = line[6:].strip().decode('ascii')
        if len(buffer) > 8192:
            raise ValueError('Wake line too large')


class OrchestratorNotifications:
    def __init__(self):
        self._lock = Lock()
        self._stop = Event()
        self._subscriptions = []
        self._version = 0
        self._pending = False
        self._response = None
        self._thread = None
        atexit.register(self.close)

    def update_subscriptions(self, subscriptions):
        subscriptions = sorted(set(subscriptions))
        if len(subscriptions) > 256:
            raise ValueError('Too many wake subscriptions')
        with self._lock:
            changed = subscriptions != self._subscriptions
            if changed:
                self._subscriptions = subscriptions
                self._version += 1
            if changed:
                self._pending = True
            if self._thread is None:
                self._thread = Thread(target=self._receive, name='tf-wakes', daemon=True)
                self._thread.start()

    def consume_wake(self):
        with self._lock:
            pending, self._pending = self._pending, False
        return pending

    def _signal(self):
        with self._lock:
            self._pending = True

    def _receive(self):
        delay = 0.5
        with requests.Session() as client:
            while not self._stop.is_set():
                with self._lock:
                    subscriptions, version = self._subscriptions[:], self._version
                try:
                    with client.post(config.INTERNAL_SERVICE_URL + '/wakes', headers=headers(),
                                     json={'subscriptions': subscriptions}, stream=True, timeout=(2, 35)) as response:
                        response.raise_for_status()
                        if not response.headers.get('Content-Type', '').startswith('text/event-stream'):
                            raise ValueError('Invalid wake content type')
                        with self._lock:
                            self._response = response
                            current = version == self._version
                        if not current:
                            continue
                        delay = 0.5
                        self._signal()  # Recover durable work missed before connect/reconnect.
                        def chunks():
                            for chunk in response.iter_content(chunk_size=1):
                                with self._lock:
                                    if self._stop.is_set() or version != self._version:
                                        return
                                yield chunk
                        for _ in wake_events(chunks()):
                            if self._stop.is_set():
                                return
                            self._signal()
                except (requests.RequestException, ValueError, OSError):
                    pass  # No SQL/logging on this thread; synchronous safety polling stays authoritative.
                finally:
                    with self._lock:
                        self._response = None
                if self._stop.wait(delay * random.uniform(0.8, 1.2)):
                    return
                delay = min(delay * 2, 30)

    def close(self):
        self._stop.set()
        with self._lock:
            thread = self._thread
        if thread is not None:
            # shortcut: daemon I/O may finish at the read deadline; never block the agent on socket close.
            thread.join(timeout=3)
