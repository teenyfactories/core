"""Shared per-process PostgreSQL connection. INTERNAL ONLY — not exported.

Every tf-core component (message queue provider, collection reads/writes,
claims, log handler, usage recorder, breakpoints) shares ONE psycopg2
connection per agent process, minted here. Before 2026-06-10 each component
opened its own (3 persistent + breakpoint churn), so total DB connections
scaled ~3-5x agent count.

Contract:
  • get_connection() — lazy connect via config.connect_postgres() (AUTOCOMMIT
    + RLS `SET app.factory_name`). Reconnects automatically if the connection
    was invalidated or closed; the SET re-applies because reconnect routes
    through the same helper.
  • cursor() — fresh cursor per operation. Components must NOT hold
    long-lived cursors; per-call cursors make reconnect state-free.
  • generation() — bumped on every successful (re)connect. The message-queue
    provider compares it to know when LISTEN must be re-issued.
  • invalidate_if_dead(exc) — call from except blocks around DB operations.
    Only connection-class errors (OperationalError / InterfaceError) close +
    clear the connection. SQL errors (constraint violations etc.) must NOT
    invalidate — with one shared connection they would kill the LISTEN
    session for the whole agent.

Threading: agents are single-threaded (handlers, schedules, and MCP dispatch
all run inline in run_pending) so there is deliberately no lock. If factory
authors spawn their own threads, psycopg2's threadsafety level 2 allows
sharing the *connection* across threads as long as cursors aren't shared —
which the fresh-cursor-per-operation discipline already guarantees.

Logging: this module must never log through the tf logger. The Postgres log
handler writes through this module; a connect failure that logged through it
would recurse. Failures simply raise to the caller.
"""

from typing import Optional

from . import config

_conn = None
_generation = 0


def get_connection():
    """Return the shared connection, (re)connecting lazily if needed."""
    global _conn, _generation
    if _conn is None or _conn.closed:
        _conn = config.connect_postgres()
        _generation += 1
    return _conn


def cursor():
    """Fresh cursor on the shared connection. One per operation."""
    return get_connection().cursor()


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
