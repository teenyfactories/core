"""Logging implementation for teenyfactories.

Public API: five functions, all the same shape — `(message: str)`.

    tf.log_debug(message)
    tf.log_info(message)
    tf.log_warn(message)
    tf.log_error(message)
    tf.log_persona(message)

Each writes to stdout via the stdlib `logging` module AND, when configured,
into the `factory_logs` PostgreSQL table via `PostgresLogHandler`.
`log_persona` writes a separate `level='persona'` row used by the chat UI
for first-person speech-bubble rendering.
"""

import json
import logging

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
        self._container_id = _config.AGENT_ID or None
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

            cursor.execute(
                """INSERT INTO factory_logs (factory_name, service_name, container_id, level, message)
                   VALUES (%s, %s, %s, %s, %s)""",
                (self._factory_name, self._service_name, self._container_id, level, message)
            )

            notify_payload = json.dumps({
                'factory_name': self._factory_name,
                'service_name': self._service_name,
                'container_id': self._container_id,
                'level': level,
                'message': message
            })
            if len(notify_payload) > 7900:
                notify_payload = notify_payload[:7900]
            cursor.execute('NOTIFY "factory_logs", %s', (notify_payload,))

        except Exception:
            # Never let logging errors crash the agent.
            pass


def log_debug(message: str):
    logger.debug(message)


def log_info(message: str):
    logger.info(message)


def log_warn(message: str):
    logger.warning(message)


def log_error(message: str):
    logger.error(message)


def log_persona(message: str):
    """
    First-person speech-bubble line for the chat UI.

    Stdout: emitted at INFO level.
    PostgreSQL: written as a separate row with level='persona' so the UI can
    render it differently from regular log lines.
    """
    # Suppress the PostgresLogHandler for the stdout emit — we write the
    # persona row separately below.
    for handler in logger.handlers:
        if isinstance(handler, PostgresLogHandler):
            handler._suppress = True

    logger.info(message)

    try:
        for handler in logger.handlers:
            if isinstance(handler, PostgresLogHandler):
                cursor = handler._get_connection()
                if cursor:
                    from .. import config as _config
                    factory = _config.FACTORY_NAME
                    service = _config.AGENT_NAME
                    container = _config.AGENT_ID or None

                    cursor.execute(
                        """INSERT INTO factory_logs (factory_name, service_name, container_id, level, message)
                           VALUES (%s, %s, %s, %s, %s)""",
                        (factory, service, container, 'persona', message)
                    )

                    notify = json.dumps({
                        'factory_name': factory, 'service_name': service,
                        'container_id': container, 'level': 'persona', 'message': message
                    })
                    cursor.execute('NOTIFY "factory_logs", %s', (notify[:7900],))
                break
    except Exception:
        pass
