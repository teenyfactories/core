"""Shared per-process PostgreSQL connection. INTERNAL ONLY — not exported.

The PostgreSQL datastore shares one psycopg2 client connection per agent.
PgBouncer can multiplex its short transactions onto a bounded server pool.

Contract:
  • transaction() serializes BEGIN, transaction-local factory scope, operation
    and COMMIT. Failure rolls back; a failed rollback discards the connection.
  • session() serializes direct PostgreSQL notification access. Pooled agents
    use the independent SSE provider, which never touches this connection.
  • get_connection() reconnects lazily; generation() tracks each new session
    so direct notifications can restore LISTEN after reconnect.
  • invalidate_if_dead(exc) discards connection-class failures. SQL errors
    leave a successfully rolled-back session available for reuse.

The reentrant lock covers whole operations, not individual statements. Nested
transactions are rejected. Handlers, embeddings, HTTP calls and breakpoint waits
run outside transactions; holding one would pin a pooler backend.

Logging: this module must never log through the tf logger. The Postgres log
handler writes through this module; a connect failure that logged through it
would recurse. Failures simply raise to the caller.
"""

from contextlib import contextmanager
from threading import RLock
from typing import Optional

from . import config

_conn = None
_generation = 0
_lock = RLock()


def get_connection():
    """Return the shared connection, (re)connecting lazily if needed."""
    global _conn, _generation
    if _conn is None or _conn.closed:
        _conn = config.connect_postgres()
        _generation += 1
    return _conn


@contextmanager
def session():
    """Exclusive direct-session access for PostgreSQL notification reception."""
    with _lock:
        yield get_connection()


@contextmanager
def transaction():
    """A complete short, factory-scoped transaction; no logging/network waits inside."""
    import psycopg2.extensions

    with _lock:
        conn = get_connection()
        if conn.get_transaction_status() != psycopg2.extensions.TRANSACTION_STATUS_IDLE:
            raise RuntimeError("Nested datastore transactions are not supported")
        cur = conn.cursor()
        try:
            cur.execute("BEGIN")
            cur.execute("SELECT set_config('app.factory_name', %s, true)", (config.FACTORY_NAME,))
            yield cur
            cur.execute("COMMIT")
        except BaseException as exc:
            try:
                cur.execute("ROLLBACK")
            except Exception:
                conn.close()
            invalidate_if_dead(exc)
            raise
        finally:
            cur.close()


def generation() -> int:
    """Connection generation — bumped on every successful (re)connect."""
    return _generation


def invalidate_if_dead(exc: Optional[BaseException]) -> None:
    """Close + clear the shared connection IFF exc is a connection-class
    error. SQL errors leave the connection alone (see module docstring)."""
    global _conn
    try:
        import psycopg2
    except ImportError:
        return
    if not isinstance(exc, (psycopg2.OperationalError, psycopg2.InterfaceError)):
        return
    if _conn is None:
        return
    try:
        _conn.close()
    except Exception:
        pass
    _conn = None
