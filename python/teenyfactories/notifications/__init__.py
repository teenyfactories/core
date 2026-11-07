"""Advisory wake providers, selected independently of durable datastore operations."""
from functools import lru_cache

from .. import config


@lru_cache(maxsize=None)
def _select(name):
    if name == 'postgres':
        from .postgres import PostgresNotifications
        return PostgresNotifications()
    if name == 'orchestrator':
        from .orchestrator import OrchestratorNotifications
        return OrchestratorNotifications()
    raise ValueError(f'Unsupported FACTORY_NOTIFICATION_BACKEND: {name}')


def get_notifications():
    return _select(config.FACTORY_NOTIFICATION_BACKEND)
