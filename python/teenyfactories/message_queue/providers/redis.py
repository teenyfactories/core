"""Redis message queue provider implementation"""

import json
from typing import List, Optional

try:
    import redis
except ImportError:
    redis = None

from teenyfactories.config import REDIS_HOST, REDIS_PORT, REDIS_DB, FACTORY_PREFIX
from teenyfactories.logging import log_info, log_error, log_warn
from teenyfactories.message_queue.base import MessageQueueProvider


class RedisProvider(MessageQueueProvider):
    """Redis implementation of message queue provider using pub/sub"""

    def __init__(self):
        self.connection = None
        self.pubsub = None

    def connect(self):
        """Establish connection to Redis"""
        if redis is None:
            raise ImportError("redis library not available - install with 'pip install redis'")

        try:
            self.connection = redis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                db=REDIS_DB,
                decode_responses=True
            )
            # Test connection
            self.connection.ping()
            log_info(f"🔌 Connected to Redis at {REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}")

        except Exception as e:
            log_error(f"❌ Failed to connect to Redis: {e}")
            raise

    def send(self, topic: str, payload: dict):
        """Send a message to a topic via Redis pub/sub"""
        try:
            if not self.connection:
                self.connect()

            self.connection.publish(topic, json.dumps(payload))

        except Exception as e:
            log_error(f"❌ Failed to send message to Redis topic {topic}: {e}")
            raise

    def subscribe(self, topics: List[str]):
        """Subscribe to topics via Redis pub/sub"""
        try:
            if not self.connection:
                self.connect()

            # Create pubsub instance if not exists
            if not self.pubsub:
                self.pubsub = self.connection.pubsub()

            # Subscribe to specific topics or pattern
            if topics:
                for topic in topics:
                    self.pubsub.subscribe(topic)
            else:
                # Subscribe to all topics with factory prefix pattern
                pattern = f"{FACTORY_PREFIX}:*" if FACTORY_PREFIX else "*"
                self.pubsub.psubscribe(pattern)

            # Get subscription confirmation
            self.pubsub.get_message(timeout=1)

        except Exception as e:
            log_error(f"❌ Failed to subscribe to Redis topics: {e}")
            raise

    def receive_one(self, timeout: float = 0.1) -> Optional[dict]:
        """Receive a single message from Redis pub/sub"""
        try:
            if not self.pubsub:
                return None

            message = self.pubsub.get_message(timeout=timeout)

            if message and message['type'] in ['message', 'pmessage']:
                try:
                    return json.loads(message['data'])
                except json.JSONDecodeError:
                    log_warn(f"⚠️ Failed to decode message: {message['data']}")
                    return None

            return None

        except Exception as e:
            log_error(f"❌ Error receiving message from Redis: {e}")
            return None

    def set_key(self, key: str, value: str, expiration: Optional[int] = None):
        """Set a key-value pair in Redis"""
        try:
            if not self.connection:
                self.connect()

            if expiration:
                self.connection.set(key, value, ex=expiration)
            else:
                self.connection.set(key, value)

        except Exception as e:
            log_error(f"❌ Failed to set Redis key {key}: {e}")
            raise

    def get_key(self, key: str) -> Optional[str]:
        """Get a value by key from Redis"""
        try:
            if not self.connection:
                self.connect()

            return self.connection.get(key)

        except Exception as e:
            log_error(f"❌ Failed to get Redis key {key}: {e}")
            return None

    def delete_key(self, key: str):
        """Delete a key from Redis"""
        try:
            if not self.connection:
                self.connect()

            self.connection.delete(key)

        except Exception as e:
            log_error(f"❌ Failed to delete Redis key {key}: {e}")
