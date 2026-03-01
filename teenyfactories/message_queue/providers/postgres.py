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

    # =========================================================================
    # Job Queue Methods (claim-based queue for state pub/sub)
    # =========================================================================

    def publish_job(self, factory_name: str, state_name: str, payload: dict, priority: int = 0):
        """
        Publish a job to the queue (insert into jobs table + NOTIFY).

        This is the claim-based equivalent of send_message for states.
        """
        try:
            if not self.connection:
                self.connect()

            # Insert job into jobs table
            self.cursor.execute("""
                INSERT INTO jobs (factory_name, state_name, payload, priority)
                VALUES (%s, %s, %s, %s)
                RETURNING id
            """, (factory_name, state_name, json.dumps(payload), priority))

            job_id = self.cursor.fetchone()[0]

            # Notify listeners that a job is available
            channel = f"job_available_{state_name}".replace('-', '_').replace(':', '_')
            self.cursor.execute(f"NOTIFY job_available, %s", (
                json.dumps({'job_id': job_id, 'factory_name': factory_name, 'state_name': state_name}),
            ))

            return job_id

        except Exception as e:
            log_error(f"❌ Failed to publish job: {e}")
            raise

    def claim_job(self, topics: List[str], worker_id: str, timeout_seconds: int = 300) -> Optional[dict]:
        """
        Atomically claim a job from the queue using FOR UPDATE SKIP LOCKED.

        Args:
            topics: List of state names to claim jobs for
            worker_id: Unique identifier for this worker/container
            timeout_seconds: How long before the job is considered stale

        Returns:
            Job dict with id, state_name, payload, etc. or None if no jobs available
        """
        try:
            if not self.connection:
                self.connect()

            # Build topic filter
            topic_placeholders = ', '.join(['%s'] * len(topics))

            # Atomically claim a job using FOR UPDATE SKIP LOCKED
            self.cursor.execute(f"""
                UPDATE jobs
                SET claimed_at = NOW(),
                    claimed_by = %s
                WHERE id = (
                    SELECT id FROM jobs
                    WHERE state_name IN ({topic_placeholders})
                    AND claimed_at IS NULL
                    AND completed_at IS NULL
                    AND failed_at IS NULL
                    ORDER BY priority DESC, created_at ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                RETURNING id, factory_name, state_name, payload, priority, created_at, retry_count
            """, (worker_id, *topics))

            row = self.cursor.fetchone()

            if row:
                return {
                    'id': row[0],
                    'factory_name': row[1],
                    'state_name': row[2],
                    'payload': json.loads(row[3]) if isinstance(row[3], str) else row[3],
                    'priority': row[4],
                    'created_at': row[5].isoformat() if row[5] else None,
                    'retry_count': row[6],
                }

            return None

        except Exception as e:
            log_error(f"❌ Failed to claim job: {e}")
            return None

    def complete_job(self, job_id: int):
        """Mark a job as completed"""
        try:
            if not self.connection:
                self.connect()

            self.cursor.execute("""
                UPDATE jobs
                SET completed_at = NOW()
                WHERE id = %s
            """, (job_id,))

        except Exception as e:
            log_error(f"❌ Failed to complete job {job_id}: {e}")
            raise

    def fail_job(self, job_id: int, error_message: str):
        """
        Mark a job as failed. If under max_retries, return it to the queue.
        """
        try:
            if not self.connection:
                self.connect()

            # Get current retry count and max retries
            self.cursor.execute("""
                SELECT retry_count, max_retries FROM jobs WHERE id = %s
            """, (job_id,))

            row = self.cursor.fetchone()
            if not row:
                return

            retry_count, max_retries = row

            if retry_count < max_retries:
                # Return to queue with incremented retry count
                self.cursor.execute("""
                    UPDATE jobs
                    SET claimed_at = NULL,
                        claimed_by = NULL,
                        retry_count = retry_count + 1,
                        error_message = %s
                    WHERE id = %s
                """, (error_message, job_id))
            else:
                # Permanently fail the job
                self.cursor.execute("""
                    UPDATE jobs
                    SET failed_at = NOW(),
                        error_message = %s
                    WHERE id = %s
                """, (error_message, job_id))

        except Exception as e:
            log_error(f"❌ Failed to fail job {job_id}: {e}")
            raise

    def recover_stale_jobs(self, timeout_seconds: int = 300):
        """
        Return timed-out jobs to the queue for reprocessing.
        Called periodically to handle crashed workers.
        """
        try:
            if not self.connection:
                self.connect()

            self.cursor.execute("""
                UPDATE jobs
                SET claimed_at = NULL,
                    claimed_by = NULL,
                    retry_count = retry_count + 1
                WHERE claimed_at IS NOT NULL
                AND completed_at IS NULL
                AND failed_at IS NULL
                AND claimed_at < NOW() - INTERVAL '%s seconds'
                AND retry_count < max_retries
                RETURNING id
            """, (timeout_seconds,))

            recovered = self.cursor.fetchall()
            if recovered:
                log_info(f"♻️ Recovered {len(recovered)} stale jobs")

            return len(recovered)

        except Exception as e:
            log_error(f"❌ Failed to recover stale jobs: {e}")
            return 0

    def get_queue_stats(self, factory_name: str = None) -> dict:
        """Get statistics about the job queue"""
        try:
            if not self.connection:
                self.connect()

            factory_filter = "WHERE factory_name = %s" if factory_name else ""
            params = (factory_name,) if factory_name else ()

            self.cursor.execute(f"""
                SELECT
                    COUNT(*) FILTER (WHERE claimed_at IS NULL AND completed_at IS NULL AND failed_at IS NULL) as pending,
                    COUNT(*) FILTER (WHERE claimed_at IS NOT NULL AND completed_at IS NULL AND failed_at IS NULL) as processing,
                    COUNT(*) FILTER (WHERE completed_at IS NOT NULL) as completed,
                    COUNT(*) FILTER (WHERE failed_at IS NOT NULL) as failed
                FROM jobs
                {factory_filter}
            """, params)

            row = self.cursor.fetchone()
            return {
                'pending': row[0] or 0,
                'processing': row[1] or 0,
                'completed': row[2] or 0,
                'failed': row[3] or 0,
            }

        except Exception as e:
            log_error(f"❌ Failed to get queue stats: {e}")
            return {'pending': 0, 'processing': 0, 'completed': 0, 'failed': 0}

    # =========================================================================
    # AND Conditional State Methods
    # =========================================================================

    def has_recent_job(self, factory_name: str, state_name: str, window_seconds: int = 300) -> bool:
        """
        Check if there's a recent completed job for a given state.

        Used for AND condition evaluation - an AND condition is satisfied
        when ALL required input states have recent completed jobs.

        Args:
            factory_name: Factory to check
            state_name: State name to check
            window_seconds: Time window in seconds (default 5 minutes)

        Returns:
            True if there's a completed job within the time window
        """
        try:
            if not self.connection:
                self.connect()

            self.cursor.execute("""
                SELECT EXISTS(
                    SELECT 1 FROM jobs
                    WHERE factory_name = %s
                    AND state_name = %s
                    AND completed_at IS NOT NULL
                    AND completed_at > NOW() - INTERVAL '%s seconds'
                )
            """, (factory_name, state_name, window_seconds))

            row = self.cursor.fetchone()
            return row[0] if row else False

        except Exception as e:
            log_error(f"❌ Failed to check recent job for {state_name}: {e}")
            return False

    def check_and_conditions(
        self,
        factory_name: str,
        required_states: List[str],
        window_seconds: int = 300
    ) -> bool:
        """
        Check if AND conditions are satisfied for an action.

        An AND condition is satisfied when ALL required input states
        have recent completed jobs within the time window.

        Args:
            factory_name: Factory name
            required_states: List of state names that must all have recent jobs
            window_seconds: Time window in seconds (default 5 minutes)

        Returns:
            True if all required states have recent completed jobs
        """
        if not required_states:
            return True  # No conditions = always satisfied

        try:
            if not self.connection:
                self.connect()

            # Check all states in a single query for efficiency
            placeholders = ', '.join(['%s'] * len(required_states))
            self.cursor.execute(f"""
                SELECT state_name FROM jobs
                WHERE factory_name = %s
                AND state_name IN ({placeholders})
                AND completed_at IS NOT NULL
                AND completed_at > NOW() - INTERVAL '%s seconds'
                GROUP BY state_name
            """, (factory_name, *required_states, window_seconds))

            found_states = {row[0] for row in self.cursor.fetchall()}
            required_set = set(required_states)

            return found_states == required_set

        except Exception as e:
            log_error(f"❌ Failed to check AND conditions: {e}")
            return False

    def wait_for_and_conditions(
        self,
        factory_name: str,
        required_states: List[str],
        window_seconds: int = 300,
        timeout_seconds: int = 600,
        poll_interval: float = 1.0
    ) -> bool:
        """
        Wait for AND conditions to be satisfied.

        Polls the database until all required states have recent completed jobs.

        Args:
            factory_name: Factory name
            required_states: List of state names that must all have recent jobs
            window_seconds: Time window for job freshness
            timeout_seconds: Maximum time to wait
            poll_interval: Time between checks in seconds

        Returns:
            True if conditions were satisfied, False if timeout
        """
        import time

        start_time = time.time()

        while time.time() - start_time < timeout_seconds:
            if self.check_and_conditions(factory_name, required_states, window_seconds):
                return True
            time.sleep(poll_interval)

        return False
