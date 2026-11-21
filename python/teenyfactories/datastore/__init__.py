"""Internal semantic datastore boundary, independent of the wake transport."""
from functools import lru_cache

from .. import config


@lru_cache(maxsize=None)
def _select(name, factory_name=None):
    if name == "postgres":
        from .postgres import PostgresDatastore
        return PostgresDatastore(factory_name)
    raise ValueError(f"Unsupported FACTORY_DATA_BACKEND: {name}")


def get_datastore(factory_name=None):
    return _select(config.FACTORY_DATA_BACKEND, factory_name)
