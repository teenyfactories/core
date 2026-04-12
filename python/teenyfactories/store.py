"""
Factory Data Store

Generic key-value storage for factory-specific persistent data.
Uses the factory_data table — no bespoke tables needed.

Usage:
    import teenyfactories as tf

    tf.store('preferences').set('key1', {'sentiment': 'up'})
    data = tf.store('preferences').get('key1')
    all_items = tf.store('preferences').all()
    tf.store('preferences').delete('key1')
"""

import os
import json
from typing import Optional, List, Dict, Any

from .logging import log_error


def _get_connection():
    """Get a database connection using the provider's connection."""
    from .message_queue.base import _get_provider
    provider = _get_provider()
    return provider.cursor, os.getenv('FACTORY_PREFIX', '')


class StoreCollection:
    """Fluent interface for a named collection within factory_data."""

    def __init__(self, collection: str):
        self._collection = collection

    def set(self, key: str, value: dict):
        """Upsert a value by key."""
        try:
            cursor, factory_name = _get_connection()
            cursor.execute(
                """INSERT INTO factory_data (factory_name, collection, key, value, updated_at)
                   VALUES (%s, %s, %s, %s, NOW())
                   ON CONFLICT (factory_name, collection, key)
                   DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()""",
                (factory_name, self._collection, key, json.dumps(value))
            )
        except Exception as e:
            log_error(f"store.set failed: {e}")
            raise

    def get(self, key: str) -> Optional[dict]:
        """Get a value by key. Returns None if not found."""
        try:
            cursor, factory_name = _get_connection()
            cursor.execute(
                "SELECT value FROM factory_data WHERE factory_name = %s AND collection = %s AND key = %s",
                (factory_name, self._collection, key)
            )
            row = cursor.fetchone()
            if row:
                return row[0] if isinstance(row[0], dict) else json.loads(row[0])
            return None
        except Exception as e:
            log_error(f"store.get failed: {e}")
            return None

    def all(self) -> List[Dict[str, Any]]:
        """Get all items in the collection. Returns list of {key, value}."""
        try:
            cursor, factory_name = _get_connection()
            cursor.execute(
                "SELECT key, value FROM factory_data WHERE factory_name = %s AND collection = %s ORDER BY updated_at DESC",
                (factory_name, self._collection)
            )
            rows = cursor.fetchall()
            return [
                {'key': row[0], 'value': row[1] if isinstance(row[1], dict) else json.loads(row[1])}
                for row in rows
            ]
        except Exception as e:
            log_error(f"store.all failed: {e}")
            return []

    def delete(self, key: str):
        """Delete a value by key."""
        try:
            cursor, factory_name = _get_connection()
            cursor.execute(
                "DELETE FROM factory_data WHERE factory_name = %s AND collection = %s AND key = %s",
                (factory_name, self._collection, key)
            )
        except Exception as e:
            log_error(f"store.delete failed: {e}")
            raise


def store(collection: str) -> StoreCollection:
    """Access a named data collection for this factory.

    Usage:
        tf.store('preferences').set('key', {'data': 'here'})
        tf.store('preferences').get('key')
    """
    return StoreCollection(collection)
