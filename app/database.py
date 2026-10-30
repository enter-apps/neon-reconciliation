"""Read and reconcile active Neon cases in PostgreSQL."""

from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta, timezone

import psycopg2
from psycopg2.extras import Json, RealDictCursor

from .reconcile import BUSINESS_FIELDS, normalize, preview, validate_decisions


TABLE = '"ai_deployment"."neon_reconciliation"'
COLUMNS = ("legal_folder", *BUSINESS_FIELDS, "deleted_at")
SELECT_COLUMNS = ", ".join(f'"{column}"' for column in COLUMNS)
SELECT_ACTIVE = (
    f"SELECT {SELECT_COLUMNS} "
    f"FROM {TABLE} WHERE \"deleted_at\" IS NULL"
)


class DatabaseReadError(Exception):
    """The existing cases could not be read."""


class DatabaseWriteError(Exception):
    """The reconciliation could not be applied."""


def connection_url() -> str:
    dsn = os.environ.get("ENTER_POSTGRES_AID_URL")
    if not dsn:
        raise DatabaseReadError("Configure ENTER_POSTGRES_AID_URL para consultar os casos existentes")
    return dsn


def load_existing_cases() -> list[dict]:
    try:
        connection = psycopg2.connect(connection_url(), connect_timeout=5)
        try:
            connection.set_session(readonly=True)
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(SELECT_ACTIVE)
                return [normalize(dict(row)) for row in cursor.fetchall()]
        finally:
            connection.close()
    except psycopg2.Error as error:
        raise DatabaseReadError("Não foi possível ler os casos do banco de dados") from error


def _value(field: str, value):
    return Json(value if value is not None else []) if field in {"requisitions", "documents"} else value


def apply_reconciliation(cases: list[dict], report_keys: list[int], decisions: dict) -> dict[str, int]:
    """Apply one reviewed run atomically and return counts from affected rows."""
    connection = None
    try:
        connection = psycopg2.connect(connection_url(), connect_timeout=5)
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            # Lock active rows while recomputing the comparison, so an old preview
            # cannot overwrite a change made since the upload was processed.
            cursor.execute(SELECT_ACTIVE + " FOR UPDATE")
            current = [normalize(dict(row)) for row in cursor.fetchall()]
            comparison = preview(cases, report_keys, current)
            resolved = validate_decisions(comparison["missing"], decisions)
            stats = {"inserted": 0, "updated": 0, "removed": 0, "unchanged": len(comparison["unchanged"])}

            columns = ("legal_folder", *BUSINESS_FIELDS)
            quoted = ", ".join(f'"{column}"' for column in columns)
            placeholders = ", ".join(["%s"] * len(columns))
            insert_sql = (
                f'INSERT INTO {TABLE} ({quoted}, "created_at", "updated_at") '
                f'VALUES ({placeholders}, now(), now()) '
                'ON CONFLICT ("legal_folder") WHERE "deleted_at" IS NULL DO NOTHING '
                'RETURNING "legal_folder"'
            )
            for case in comparison["new"]:
                cursor.execute(insert_sql, tuple(_value(field, case.get(field)) for field in columns))
                if cursor.fetchone() is None:
                    raise DatabaseWriteError("Os casos mudaram durante a reconciliação, processe o arquivo novamente")
                stats["inserted"] += 1

            for entry in comparison["updated"]:
                case = entry["case"]
                changed = entry["changed_fields"]
                assignments = ", ".join(f'"{field}" = %s' for field in changed)
                cursor.execute(
                    f'UPDATE {TABLE} SET {assignments}, "updated_at" = now() '
                    'WHERE "legal_folder" = %s AND "deleted_at" IS NULL RETURNING "legal_folder"',
                    tuple(_value(field, case.get(field)) for field in changed) + (case["legal_folder"],),
                )
                if cursor.fetchone() is None:
                    raise DatabaseWriteError("Os casos mudaram durante a reconciliação, processe o arquivo novamente")
                stats["updated"] += 1

            for folder, decision in resolved.items():
                if decision["action"] != "remove":
                    continue
                removal_day = date.fromisoformat(decision["deleted_at"])
                removal_at = datetime.combine(removal_day, time(18), timezone(timedelta(hours=-3)))
                cursor.execute(
                    f'UPDATE {TABLE} SET "deleted_at" = %s '
                    'WHERE "legal_folder" = %s AND "deleted_at" IS NULL RETURNING "legal_folder"',
                    (removal_at, folder),
                )
                if cursor.fetchone() is None:
                    raise DatabaseWriteError("Os casos mudaram durante a reconciliação, processe o arquivo novamente")
                stats["removed"] += 1
        connection.commit()
        return stats
    except (psycopg2.Error, DatabaseWriteError, ValueError) as error:
        if connection is not None:
            connection.rollback()
        if isinstance(error, (DatabaseWriteError, ValueError)):
            raise
        raise DatabaseWriteError("Não foi possível aplicar a reconciliação no banco de dados") from error
    finally:
        if connection is not None:
            connection.close()
