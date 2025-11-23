"""PostgreSQL message queue provider implementation using LISTEN/NOTIFY"""

import json
import select
from typing import List, Optional

try:
    import psycopg2
    import psycopg2.extensions
except ImportError:
    psycopg2 = None

from teenyfactories.logging import log_info, log_error, log_warn
from teenyfactories.message_queue.base import MessageQueueProvider


class PostgresProvider(MessageQueueProvider):
    """PostgreSQL implementation of message queue provider using LISTEN/NOTIFY"""

    def __init__(self):
        self.connection = None
        self.cursor = None
        self.kv_table = "teenyfactories_kv"  # Table for key-value storage

    def connect(self):
        """Establish connection to PostgreSQL"""
        if psycopg2 is None:
            raise ImportError("psycopg2 library not available - install with 'pip install psycopg2-binary'")

        try:
            import os

            # Get PostgreSQL connection details from environment
            pg_host = os.getenv('POSTGRES_HOST', 'postgres')
            pg_port = int(os.getenv('POSTGRES_PORT', '5432'))
            pg_db = os.getenv('POSTGRES_DB', 'teenyfactories')
            pg_user = os.getenv('POSTGRES_USER', 'postgres')
            pg_password = os.getenv('POSTGRES_PASSWORD', 'postgres')

            self.connection = psycopg2.connect(
                host=pg_host,
                port=pg_port,
                database=pg_db,
                user=pg_user,
                password=pg_password
            )

            # Set connection to autocommit mode for LISTEN/NOTIFY
            self.connection.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
            self.cursor = self.connection.cursor()

            # Create key-value storage table if it doesn't exist
            self._create_kv_table()

            log_info(f"🔌 Connected to PostgreSQL at {pg_host}:{pg_port}/{pg_db}")

        except Exception as e:
            log_error(f"❌ Failed to connect to PostgreSQL: {e}")
            raise

    def _create_kv_table(self):
        """Create key-value storage table if it doesn't exist"""
        try:
            self.cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS {self.kv_table} (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    expires_at TIMESTAMP
                )
            """)

            # Create index on expires_at for efficient cleanup
            self.cursor.execute(f"""
                CREATE INDEX IF NOT EXISTS idx_{self.kv_table}_expires
                ON {self.kv_table}(expires_at)
            """)

        except Exception as e:
            log_error(f"❌ Failed to create key-value table: {e}")
            raise

    def send(self, topic: str, payload: dict):
        """Send a message to a topic via PostgreSQL NOTIFY"""
        try:
            if not self.connection:
                self.connect()

            # PostgreSQL channel names must be valid identifiers
            # Replace special characters with underscores
            channel = topic.replace(':', '_').replace('-', '_')

            # NOTIFY payload is limited to 8000 bytes in PostgreSQL
            payload_json = json.dumps(payload)
            if len(payload_json) > 7900:  # Leave some margin
                log_warn(f"⚠️ Message payload too large for PostgreSQL NOTIFY ({len(payload_json)} bytes)")
                # Could implement chunking or use a different approach for large messages

            self.cursor.execute(f"NOTIFY {channel}, %s", (payload_json,))

        except Exception as e:
            log_error(f"❌ Failed to send message to PostgreSQL channel {topic}: {e}")
            raise

    def subscribe(self, topics: List[str]):
        """Subscribe to topics via PostgreSQL LISTEN"""
        try:
            if not self.connection:
                self.connect()

            if topics:
                for topic in topics:
                    # Convert topic to valid PostgreSQL channel name
                    channel = topic.replace(':', '_').replace('-', '_')
                    self.cursor.execute(f"LISTEN {channel}")
            else:
                # PostgreSQL doesn't support wildcard LISTEN
                # Would need to implement pattern matching differently
                log_warn("⚠️ PostgreSQL provider doesn't support wildcard subscriptions")

        except Exception as e:
            log_error(f"❌ Failed to subscribe to PostgreSQL channels: {e}")
            raise

    def receive_one(self, timeout: float = 0.1) -> Optional[dict]:
        """Receive a single message from PostgreSQL NOTIFY"""
        try:
            if not self.connection:
                return None

            # Use select to wait for notifications with timeout
            if select.select([self.connection], [], [], timeout) == ([], [], []):
                # Timeout - no notifications
                return None

            # Poll for notifications
            self.connection.poll()

            # Get notifications
            while self.connection.notifies:
                notify = self.connection.notifies.pop(0)
                try:
                    # Parse the JSON payload
                    payload = json.loads(notify.payload)
                    return payload
                except json.JSONDecodeError:
                    log_warn(f"⚠️ Failed to decode notification: {notify.payload}")
                    continue

            return None

        except Exception as e:
            log_error(f"❌ Error receiving message from PostgreSQL: {e}")
            return None

    def set_key(self, key: str, value: str, expiration: Optional[int] = None):
        """Set a key-value pair in PostgreSQL"""
        try:
            if not self.connection:
                self.connect()

            # Calculate expiration timestamp if provided
            expires_at = None
            if expiration:
                expires_at = f"NOW() + INTERVAL '{expiration} seconds'"

            # Upsert the key-value pair
            if expires_at:
                self.cursor.execute(f"""
                    INSERT INTO {self.kv_table} (key, value, expires_at)
                    VALUES (%s, %s, {expires_at})
                    ON CONFLICT (key) DO UPDATE
                    SET value = EXCLUDED.value, expires_at = EXCLUDED.expires_at
                """, (key, value))
            else:
                self.cursor.execute(f"""
                    INSERT INTO {self.kv_table} (key, value, expires_at)
                    VALUES (%s, %s, NULL)
                    ON CONFLICT (key) DO UPDATE
                    SET value = EXCLUDED.value, expires_at = NULL
                """, (key, value))

            # Clean up expired keys periodically
            self._cleanup_expired_keys()

        except Exception as e:
            log_error(f"❌ Failed to set PostgreSQL key {key}: {e}")
            raise

    def get_key(self, key: str) -> Optional[str]:
        """Get a value by key from PostgreSQL"""
        try:
            if not self.connection:
                self.connect()

            self.cursor.execute(f"""
                SELECT value FROM {self.kv_table}
                WHERE key = %s
                AND (expires_at IS NULL OR expires_at > NOW())
            """, (key,))

            result = self.cursor.fetchone()
            return result[0] if result else None

        except Exception as e:
            log_error(f"❌ Failed to get PostgreSQL key {key}: {e}")
            return None

    def delete_key(self, key: str):
        """Delete a key from PostgreSQL"""
        try:
            if not self.connection:
                self.connect()

            self.cursor.execute(f"""
                DELETE FROM {self.kv_table}
                WHERE key = %s
            """, (key,))

        except Exception as e:
            log_error(f"❌ Failed to delete PostgreSQL key {key}: {e}")

    def _cleanup_expired_keys(self):
        """Clean up expired keys from the key-value table"""
        try:
            self.cursor.execute(f"""
                DELETE FROM {self.kv_table}
                WHERE expires_at IS NOT NULL AND expires_at < NOW()
            """)
        except Exception as e:
            log_error(f"❌ Failed to cleanup expired keys: {e}")
