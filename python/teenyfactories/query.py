"""Lazy, datastore-neutral collection queries; filters validate before execution."""
from .where_parser import parse_where
from .logging import log_error
from .datastore import get_datastore

__all__ = ['CollectionQuery']


class CollectionQuery:
    def __init__(self, name):
        self._name = name
        self._states = None
        self._state_set = False
        self._wheres = []
        self._vector = None
        self._limit = None
        self._chunk_mode = False
        self._vec_key = None

    def _clone(self):
        q = CollectionQuery(self._name)
        q._states = list(self._states) if self._states is not None else None
        q._state_set = self._state_set
        q._wheres = list(self._wheres)
        q._vector, q._limit = self._vector, self._limit
        q._chunk_mode, q._vec_key = self._chunk_mode, self._vec_key
        return q

    def state(self, value):
        """Filter by one state or a list. Calling state twice is an error."""
        if self._state_set:
            raise ValueError('state() called more than once; combine into one .state([...]) call')
        from .collection import _validate_state
        states = [value] if isinstance(value, str) else list(value)
        if not states:
            raise ValueError('state() requires at least one state')
        for state in states:
            _validate_state(state)
        q = self._clone()
        q._states, q._state_set = states, True
        return q

    def where(self, expr):
        """AND a validated filter AST, never backend-specific SQL."""
        node = parse_where(expr)
        q = self._clone()
        q._wheres = self._wheres + [node]
        return q

    def vector_search(self, text_or_vec):
        """Resolve embeddings before any datastore transaction."""
        from .embedding import embed
        q = self._clone()
        q._vector = list(embed(text_or_vec)) if isinstance(text_or_vec, str) else list(text_or_vec)
        if q._limit is None:
            q._limit = 10
        return q

    def chunks(self):
        q = self._clone()
        q._chunk_mode = True
        return q

    def key(self, k):
        q = self._clone()
        q._vec_key = k
        return q

    def limit(self, n):
        q = self._clone()
        q._limit = int(n)
        return q

    def get_all(self):
        try:
            return get_datastore().query(self)
        except Exception as exc:
            log_error(f'collection query failed: {exc}')
            return []

    def run(self):
        return self.get_all()

    def first(self):
        rows = self.limit(1).get_all()
        return rows[0] if rows else None

    def count(self):
        try:
            return get_datastore().count(self)
        except Exception as exc:
            log_error(f'collection count failed: {exc}')
            return 0

    def __iter__(self):
        return iter(self.get_all())
