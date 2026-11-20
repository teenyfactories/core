"""PostgreSQL datastore; SQL/transactions never depend on the notification backend."""
from .postgres_rows import PostgresRows
from .postgres_query import PostgresQueries
from .postgres_work import PostgresWork
from .postgres_telemetry import PostgresTelemetry
from .. import config, db


class PostgresDatastore(PostgresRows, PostgresQueries, PostgresWork, PostgresTelemetry):
    def __init__(self, factory_name=None):
        self._factory_name = factory_name

    @property
    def factory_name(self):
        return config.FACTORY_NAME if self._factory_name is None else self._factory_name

    def transaction(self):
        return db.transaction() if self._factory_name is None else db.transaction(self.factory_name)
