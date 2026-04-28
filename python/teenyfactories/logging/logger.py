"""Logging implementation for teenyfactories"""

import os
import json
import logging

# Get logger instance (configured in config module)
logger = logging.getLogger('teenyfactories')


class PostgresLogHandler(logging.Handler):
    """
    Logging handler that writes to the factory_logs PostgreSQL table
    and sends a NOTIFY for real-time WebSocket streaming.

    Outputs go to BOTH stdout (via basicConfig) AND PostgreSQL (via this handler).
    """

    def __init__(self):
        super().__init__()
        # Lazy-import config to avoid an import cycle (config attaches this
        # handler when POSTGRES_HOST is set).
        from .. import config as _config
        self._conn = None
        self._cursor = None
        self._factory_name = _config.FACTORY_NAME
        self._service_name = _config.AGENT_NAME
        # HOSTNAME is injected by Docker itself, not by our orchestrator —
        # read from os directly.
        self._container_id = os.getenv('HOSTNAME', '')[:12] if os.getenv('HOSTNAME') else None
        self._suppress = False  # Set True to skip next emit (used by log_persona)

    def _get_connection(self):
        """Lazy connection — only connect on first log write."""
        if self._conn is not None:
            return self._cursor

        try:
            import psycopg2
            import psycopg2.extensions
            from .. import config as _config

            self._conn = psycopg2.connect(
                host=_config.POSTGRES_HOST,
                port=_config.POSTGRES_PORT,
                database=_config.POSTGRES_DB,
                user=_config.POSTGRES_USER,
                password=_config.POSTGRES_PASSWORD
            )
            self._conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
            self._cursor = self._conn.cursor()
            return self._cursor
        except Exception:
            self._conn = None
            self._cursor = None
            return None

    def _level_name(self, record):
        """Map Python log levels to our level strings."""
        name = record.levelname.lower()
        if name == 'warning':
            return 'warn'
        if name == 'critical':
            return 'error'
        return name

    def emit(self, record):
        if self._suppress:
            self._suppress = False
            return
        try:
            cursor = self._get_connection()
            if cursor is None:
                return

            level = self._level_name(record)
            message = self.format(record) if self.formatter else record.getMessage()

            # INSERT into factory_logs
            cursor.execute(
                """INSERT INTO factory_logs (factory_name, service_name, container_id, level, message)
                   VALUES (%s, %s, %s, %s, %s)""",
                (self._factory_name, self._service_name, self._container_id, level, message)
            )

            # NOTIFY for real-time streaming
            notify_payload = json.dumps({
                'factory_name': self._factory_name,
                'service_name': self._service_name,
                'container_id': self._container_id,
                'level': level,
                'message': message
            })
            # Truncate if too large for NOTIFY (8000 byte limit)
            if len(notify_payload) > 7900:
                notify_payload = notify_payload[:7900]
            cursor.execute('NOTIFY "factory_logs", %s', (notify_payload,))

        except Exception:
            # Never let logging errors crash the agent
            pass


def log(message, level='debug'):
    level = level.lower()
    if level == 'debug':
        logger.debug(message)
    elif level == 'info':
        logger.info(message)
    elif level in ('warn', 'warning'):
        logger.warning(message)
    elif level == 'error':
        logger.error(message)
    else:
        logger.debug(f"[{level.upper()}] {message}")


def log_debug(message):
    log(message, level='debug')


def log_info(message):
    log(message, level='info')


def log_warn(message):
    log(message, level='warn')


def log_error(message):
    log(message, level='error')


def log_persona(message, metadata=None):
    """
    Log a first-person message for UI speech bubbles.
    Outputs to stdout as INFO, writes to PostgreSQL as level='persona'.
    """
    # Suppress the PostgresLogHandler for the stdout emit — we write persona separately
    for handler in logger.handlers:
        if isinstance(handler, PostgresLogHandler):
            handler._suppress = True

    # Stdout as info
    logger.info(message)

    # Write directly to DB as 'persona' level
    try:
        for handler in logger.handlers:
            if isinstance(handler, PostgresLogHandler):
                cursor = handler._get_connection()
                if cursor:
                    from .. import config as _config
                    factory = _config.FACTORY_NAME
                    service = _config.AGENT_NAME
                    container = os.getenv('HOSTNAME', '')[:12] if os.getenv('HOSTNAME') else None

                    cursor.execute(
                        """INSERT INTO factory_logs (factory_name, service_name, container_id, level, message, log_data)
                           VALUES (%s, %s, %s, %s, %s, %s)""",
                        (factory, service, container, 'persona', message,
                         json.dumps(metadata) if metadata else None)
                    )

                    notify = json.dumps({
                        'factory_name': factory, 'service_name': service,
                        'container_id': container, 'level': 'persona', 'message': message
                    })
                    cursor.execute('NOTIFY "factory_logs", %s', (notify[:7900],))
                break
    except Exception:
        pass
