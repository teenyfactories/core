"""PostgreSQL message queue provider using factory_states table with in-memory cursors"""

import json
import os
from datetime import datetime, timezone
from typing import List, Optional, Dict

try:
    import psycopg2
    import psycopg2.extensions
except ImportError:
    psycopg2 = None

from teenyfactories.logging import log_info, log_error, log_warn
from teenyfactories.message_queue.base import MessageQueueProvider


class PostgresProvider(MessageQueueProvider):
    """PostgreSQL implementation using factory_states table for durable pub/sub.

    Cursors are tracked in-memory per container — no shared cursor table.
    By default, only processes states created after the container started.
    """

    def __init__(self):
        self.connection = None
        self.send_connection = None
        self.cursor = None
        self.send_cursor = None
        self._subscribed_topics = []
        self._factory_name = os.getenv('FACTORY_PREFIX', '')
        self._agent_name = os.getenv('AGENT_NAME', 'unknown')
        self._startup_time = None
        self._cursors = {}          # topic -> last_seen_at (datetime)
        self._topic_options = {}    # topic -> { on_startup_replay_latest, process_latest_only }

    def connect(self):
        if psycopg2 is None:
            raise ImportError("psycopg2 not available — install with 'pip install psycopg2-binary'")

        try:
            pg_host = os.getenv('POSTGRES_HOST', 'postgres')
            pg_port = int(os.getenv('POSTGRES_PORT', '5432'))
            pg_db = os.getenv('POSTGRES_DB', 'teenyfactories')
            pg_user = os.getenv('POSTGRES_USER', 'postgres')
            pg_password = os.getenv('POSTGRES_PASSWORD', 'postgres')

            conn_args = dict(host=pg_host, port=pg_port, database=pg_db,
                             user=pg_user, password=pg_password)

            self.connection = psycopg2.connect(**conn_args)
            self.connection.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
            self.cursor = self.connection.cursor()

            self.send_connection = psycopg2.connect(**conn_args)
            self.send_connection.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
            self.send_cursor = self.send_connection.cursor()

            self._startup_time = datetime.now(timezone.utc)
            self._ensure_tables()
            self._create_logs_table()

            log_info(f"Connected to PostgreSQL at {pg_host}:{pg_port}/{pg_db}")

        except Exception as e:
            log_error(f"Failed to connect to PostgreSQL: {e}")
            raise

    def _ensure_tables(self):
        """Ensure factory_states table exists."""
        try:
            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS factory_states (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    factory_name TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    payload JSONB NOT NULL DEFAULT '{}',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            self.cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_factory_states_lookup
                ON factory_states(factory_name, topic, created_at)
            """)
        except Exception as e:
            log_error(f"Failed to create factory_states table: {e}")
            raise

    def _create_logs_table(self):
        """Create factory_logs table if it doesn't exist."""
        try:
            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS factory_logs (
                    id BIGSERIAL PRIMARY KEY,
                    factory_name TEXT NOT NULL,
                    service_name TEXT NOT NULL,
                    container_id TEXT,
                    level TEXT NOT NULL DEFAULT 'info',
                    message TEXT NOT NULL,
                    log_data JSONB,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            self.cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_factory_logs_factory
                ON factory_logs(factory_name, created_at DESC)
            """)
            self.cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_factory_logs_service
                ON factory_logs(factory_name, service_name, created_at DESC)
            """)
        except Exception as e:
            log_error(f"Failed to create factory_logs table: {e}")
            raise

    # =========================================================================
    # Send — INSERT into factory_states
    # =========================================================================

    def send(self, topic: str, payload: dict):
        """Insert a state into factory_states."""
        try:
            if not self.send_connection:
                self.connect()

            raw_topic = topic
            if self._factory_name and topic.startswith(f"{self._factory_name}."):
                raw_topic = topic[len(self._factory_name) + 1:]

            self.send_cursor.execute(
                "INSERT INTO factory_states (factory_name, topic, payload) VALUES (%s, %s, %s)",
                (self._factory_name, raw_topic, json.dumps(payload))
            )

        except Exception as e:
            log_error(f"❌ Failed to insert state for topic {topic}: {e}")
            raise

    # =========================================================================
    # Subscribe — record topics and options, set initial cursors
    # =========================================================================

    def subscribe(self, topics: List[str], options: Optional[Dict] = None):
        """Record topics to poll and their options. Set initial cursor positions."""
        if not topics:
            return

        for topic in topics:
            if topic not in self._subscribed_topics:
                self._subscribed_topics.append(topic)

            # Store options for this topic
            if options and topic in options:
                self._topic_options[topic] = options[topic]

            opts = self._topic_options.get(topic, {})

            if opts.get('on_startup_replay_latest'):
                # Don't set cursor — first poll will fetch the latest existing state
                pass
            elif topic not in self._cursors:
                # Default: only process states after startup
                self._cursors[topic] = self._startup_time

    # =========================================================================
    # Receive — poll factory_states with in-memory cursor
    # =========================================================================

    def receive_one(self, timeout: float = 0.1) -> Optional[dict]:
        """Poll factory_states for the next unprocessed state."""
        try:
            if not self.connection:
                return None

            for full_topic in self._subscribed_topics:
                # Strip factory prefix for DB query
                topic = full_topic
                if self._factory_name and full_topic.startswith(f"{self._factory_name}."):
                    topic = full_topic[len(self._factory_name) + 1:]

                opts = self._topic_options.get(full_topic, {})

                if full_topic not in self._cursors:
                    # First poll with on_startup_replay_latest — get the latest existing state
                    self.cursor.execute(
                        "SELECT id, payload, created_at FROM factory_states "
                        "WHERE factory_name = %s AND topic = %s "
                        "ORDER BY created_at DESC LIMIT 1",
                        (self._factory_name, topic)
                    )
                elif opts.get('process_latest_only'):
                    # Skip to newest state after cursor
                    self.cursor.execute(
                        "SELECT id, payload, created_at FROM factory_states "
                        "WHERE factory_name = %s AND topic = %s AND created_at > %s "
                        "ORDER BY created_at DESC LIMIT 1",
                        (self._factory_name, topic, self._cursors[full_topic])
                    )
                else:
                    # Sequential — next state after cursor
                    self.cursor.execute(
                        "SELECT id, payload, created_at FROM factory_states "
                        "WHERE factory_name = %s AND topic = %s AND created_at > %s "
                        "ORDER BY created_at ASC LIMIT 1",
                        (self._factory_name, topic, self._cursors[full_topic])
                    )

                state = self.cursor.fetchone()
                if state:
                    state_id, payload, created_at = state

                    # Advance in-memory cursor
                    self._cursors[full_topic] = created_at

                    # Parse payload
                    if isinstance(payload, str):
                        payload = json.loads(payload)

                    return payload

            return None

        except Exception as e:
            log_error(f"❌ Error polling factory_states: {e}")
            return None
