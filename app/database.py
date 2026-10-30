"""Read the active Neon cases in a PostgreSQL read-only transaction."""

from __future__ import annotations

import os

import psycopg2
from psycopg2.extras import RealDictCursor

from .reconcile import BUSINESS_FIELDS, normalize


TABLE = '"ai_deployment"."neon_reconciliation"'
COLUMNS = ("legal_folder", *BUSINESS_FIELDS, "deleted_at")
SELECT_COLUMNS = ", ".join(f'"{column}"' for column in COLUMNS)
SELECT_ACTIVE = (
    f"SELECT {SELECT_COLUMNS} "
    f"FROM {TABLE} WHERE \"deleted_at\" IS NULL"
)


class DatabaseReadError(Exception):
    """The existing cases could not be read."""


def load_existing_cases() -> list[dict]:
    dsn = os.environ.get("ENTER_POSTGRES_AID_URL")
    if not dsn:
        raise DatabaseReadError("Configure ENTER_POSTGRES_AID_URL para consultar os casos existentes")

    try:
        connection = psycopg2.connect(dsn, connect_timeout=5)
        try:
            connection.set_session(readonly=True)
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(SELECT_ACTIVE)
                return [normalize(dict(row)) for row in cursor.fetchall()]
        finally:
            connection.close()
    except psycopg2.Error as error:
        raise DatabaseReadError("Não foi possível ler os casos do banco de dados") from error
