"""Factory collections: stable tf API over the selected semantic datastore.

data= replaces the whole payload; omitted arguments preserve existing columns.
Reads retain their existing log-and-empty failure policy; writes raise.
"""
import re
import uuid
from typing import Any, Dict, List, Optional, Union

from . import config
from .datastore import get_datastore
from .logging import log_error
from .query import CollectionQuery

_ID_RE = re.compile(r'^[a-z0-9_]+$')


def _validate_collection(name):
    if not name or not _ID_RE.match(name) or len(name) > 40:
        raise ValueError(f"Invalid collection name {name!r}: must match [a-z0-9_]+ and be <= 40 chars")


def _validate_state(state):
    if not state or not _ID_RE.match(state) or len(state) > 40:
        raise ValueError(f"Invalid state {state!r}: must match [a-z0-9_]+ and be <= 40 chars")


def _check_channel_length(factory_name, collection, state):
    if len(factory_name) + len(collection) + len(state) + 2 > 63:
        raise ValueError(f"NOTIFY channel would exceed 63 chars (factory={factory_name!r} "
                         f"collection={collection!r} state={state!r})")


class Collection:
    def __init__(self, name: str):
        _validate_collection(name)
        self._name = name

    def set(self, key: str, state: Optional[str] = None, data: Optional[dict] = None) -> str:
        """Upsert a row. Passed data replaces the payload; state-only preserves it."""
        if state is None and data is None:
            raise ValueError('collection.set requires at least one of state=, data=')
        if state is not None:
            _validate_state(state)
        try:
            _check_channel_length(config.FACTORY_NAME, self._name, state or 'new')
            return get_datastore().upsert(self._name, key, state, data)
        except Exception as exc:
            log_error(f'collection.set failed: {exc}')
            raise

    def add(self, state: str, data: Optional[dict] = None) -> str:
        """Insert a fresh UUID-keyed row at the required state."""
        _validate_state(state)
        key = uuid.uuid4().hex
        try:
            _check_channel_length(config.FACTORY_NAME, self._name, state)
            return get_datastore().insert(self._name, key, state, data)
        except Exception as exc:
            log_error(f'collection.add failed: {exc}')
            raise

    def remove(self, key: str):
        """Delete one row and its associated vectors."""
        try:
            return get_datastore().remove(self._name, key)
        except Exception as exc:
            log_error(f'collection.remove failed: {exc}')
            raise

    def set_vectors(self, key: str, items: List[Union[str, Dict[str, Any]]],
                    provider: Optional[str] = None, model: Optional[str] = None) -> int:
        """Atomically replace all vectors for an existing row, embedding before SQL.

        Items are strings or {content, meta, embedding?}. [] clears vectors.
        Read and write embedding models/dimensions must match.
        """
        from .embedding import embed
        norm = [({'content': item, 'meta': {}, 'embedding': None} if isinstance(item, str)
                 else {'content': item.get('content', ''), 'meta': item.get('meta') or {},
                       'embedding': item.get('embedding')}) for item in items]
        need = [item['content'] for item in norm if item['embedding'] is None]
        if need:
            builder = embed(need)
            if provider is not None:
                builder = builder.provider(provider)
            if model is not None:
                builder = builder.model(model)
            computed = iter(list(builder))
            for item in norm:
                if item['embedding'] is None:
                    item['embedding'] = list(next(computed))
        try:
            return get_datastore().replace_vectors(self._name, key, norm)
        except Exception as exc:
            log_error(f'collection.set_vectors failed: {exc}')
            raise

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        """Return a row or None; preserve the current fail-soft read contract."""
        try:
            return get_datastore().get(self._name, key)
        except Exception as exc:
            log_error(f'collection.get failed: {exc}')
            return None

    def exists(self, key: str) -> bool:
        try:
            return get_datastore().exists(self._name, key)
        except Exception as exc:
            log_error(f'collection.exists failed: {exc}')
            return False

    def state(self, value):
        return CollectionQuery(self._name).state(value)

    def where(self, expr: str):
        return CollectionQuery(self._name).where(expr)

    def limit(self, n: int):
        return CollectionQuery(self._name).limit(n)

    def get_all(self):
        return CollectionQuery(self._name).get_all()

    def count(self) -> int:
        return CollectionQuery(self._name).count()

    def first(self):
        return CollectionQuery(self._name).first()

    def vector_search(self, text_or_vec):
        """Start lazy ANN search; limit/run, chunks and key remain chainable."""
        return CollectionQuery(self._name).vector_search(text_or_vec)


def collection(name: str) -> Collection:
    return Collection(name)
