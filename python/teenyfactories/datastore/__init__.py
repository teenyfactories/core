"""Internal semantic datastore boundary, independent of the wake transport."""
from functools import lru_cache

from .. import config


@lru_cache(maxsize=None)
def _select(name):
    if name == "postgres":
        from .postgres import PostgresDatastore
        return PostgresDatastore()
    raise ValueError(f"Unsupported FACTORY_DATA_BACKEND: {name}")


def get_datastore():
    return _select(config.FACTORY_DATA_BACKEND)
