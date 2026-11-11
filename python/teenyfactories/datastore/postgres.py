"""PostgreSQL datastore; SQL/transactions never depend on the notification backend."""
from .postgres_rows import PostgresRows
from .postgres_query import PostgresQueries
from .postgres_work import PostgresWork
from .postgres_telemetry import PostgresTelemetry


class PostgresDatastore(PostgresRows, PostgresQueries, PostgresWork, PostgresTelemetry):
    pass
