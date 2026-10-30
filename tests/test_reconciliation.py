import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from email.message import Message
from pathlib import Path
from unittest.mock import patch

import openpyxl
import psycopg2

from app import database, server
from app.consolidation import load_mapping


class ReconciliationWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        data = Path(self.directory.name)
        for target, value in (
            ("HISTORY_FILE", data / "history.json"),
            ("UPLOADS", data / "uploads"),
            ("RUNS", {}),
        ):
            mocked = patch.object(server, target, value)
            mocked.start()
            self.addCleanup(mocked.stop)

    def handler(self, route, body=None):
        handler = server.Handler.__new__(server.Handler)
        handler.path = route
        handler.body_json = lambda: body or {}

        def send_json(value, status=200):
            handler.response = (value, status)
            return handler.response

        def send_file(path, content_type, download_name=None):
            handler.response = ({"contents": path.read_bytes(), "type": content_type, "name": download_name}, 200)
            return handler.response

        handler.send_json = send_json
        handler.send_file = send_file
        return handler

    def upload(self, database_error=None):
        mapping = load_mapping()
        headers = []
        for field in mapping.all_fields:
            if headers.count(field.source) < field.occurrence:
                headers.append(field.source)
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.append(headers)
        row = [None] * len(headers)
        row[headers.index("Pasta")] = 50182
        row[headers.index("Numero atual do processo")] = "8017873-86.2026.8.05.0150"
        sheet.append(row)
        stream = io.BytesIO()
        workbook.save(stream)
        boundary = b"readonly-boundary"
        payload = (
            b"--" + boundary
            + b'\r\nContent-Disposition: form-data; name="file"; filename="relatorio.xlsx"\r\n'
            + b"Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n"
            + stream.getvalue() + b"\r\n--" + boundary + b"--\r\n"
        )
        handler = self.handler("/api/process")
        handler.headers = Message()
        handler.headers["Content-Length"] = str(len(payload))
        handler.headers["Content-Type"] = "multipart/form-data; boundary=" + boundary.decode()
        handler.rfile = io.BytesIO(payload)
        existing = [{"legal_folder": 70001, "case_number": "1234567-71.2026.8.05.0150", "deleted_at": None}]
        with patch.object(server, "load_existing_cases", return_value=existing, side_effect=database_error) as read_database:
            handler.do_POST()
        read_database.assert_called_once_with()
        return handler.response

    def test_only_completed_run_is_archived(self):
        result, status = self.upload()
        self.assertEqual(status, 200)
        self.assertEqual(len(result["comparison"]["new"]), 1)
        self.assertEqual(len(result["comparison"]["missing"]), 1)
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])
        expected_cases = server.RUNS[result["run_id"]]["cases"]

        finish = self.handler("/api/finish", {"run_id": result["run_id"], "decisions": {
            "70001": {"action": "remove", "deleted_at": "2026-10-30"}
        }})
        with patch.object(server, "apply_reconciliation", return_value={"inserted": 1, "updated": 0, "removed": 1, "unchanged": 0}) as write_database:
            finish.do_POST()
        write_database.assert_called_once_with(expected_cases, [50182], {"70001": {"action": "remove", "deleted_at": "2026-10-30"}})
        summary, status = finish.response
        self.assertEqual(status, 200)
        self.assertEqual(summary["stats"]["inserted"], 1)
        self.assertEqual(summary["stats"]["removed"], 1)
        history, status = self.handler("/api/history").do_GET()
        self.assertEqual((status, len(history)), (200, 1))
        self.assertEqual(history[0]["summary"]["removed"], 1)
        downloaded, status = self.handler(f"/api/history/{result['run_id']}/download").do_GET()
        self.assertEqual(status, 200)
        self.assertTrue(downloaded["contents"].startswith(b"PK"))

    def test_cancel_discards_in_memory_preview(self):
        result, status = self.upload()
        self.assertEqual(status, 200)
        self.assertIn(result["run_id"], server.RUNS)
        self.assertEqual(self.handler("/api/cancel", {"run_id": result["run_id"]}).do_POST(), ({"ok": True}, 200))
        self.assertNotIn(result["run_id"], server.RUNS)
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])

    def test_failed_commit_keeps_run_for_retry_and_does_not_archive(self):
        result, status = self.upload()
        self.assertEqual(status, 200)
        finish = self.handler("/api/finish", {"run_id": result["run_id"], "decisions": {}})
        with patch.object(server, "apply_reconciliation", side_effect=database.DatabaseWriteError("Banco indisponível")):
            finish.do_POST()
        self.assertEqual(finish.response, ({"error": "Banco indisponível"}, 503))
        self.assertIn(result["run_id"], server.RUNS)
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])

    def test_database_error_does_not_become_format_error_or_create_run(self):
        result, status = self.upload(database.DatabaseReadError("Banco indisponível"))
        self.assertEqual((status, result["error"]), (503, "Banco indisponível"))
        self.assertEqual(server.RUNS, {})
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])

    def test_existing_history_is_read_without_migration(self):
        data = Path(self.directory.name)
        upload_dir = data / "uploads"
        upload_dir.mkdir()
        (upload_dir / "saved.xlsx").write_bytes(b"old file")
        history = [{"id": "old", "status": "completed", "stored_name": "saved.xlsx", "name": "original.xlsx",
                    "summary": {"cases": 3, "new": 1, "updated": 1, "removed": 0, "invalid": 0}}]
        server.HISTORY_FILE.write_text(json.dumps(history))
        listed, status = self.handler("/api/history").do_GET()
        self.assertEqual(status, 200)
        self.assertEqual(listed[0]["summary"]["unchanged"], 1)
        self.assertEqual(json.loads(server.HISTORY_FILE.read_text()), history)
        downloaded, status = self.handler("/api/history/old/download").do_GET()
        self.assertEqual((status, downloaded["contents"], downloaded["name"]), (200, b"old file", "original.xlsx"))


class DatabaseReadTest(unittest.TestCase):
    def test_select_uses_read_only_transaction(self):
        class Cursor:
            def __enter__(self): return self
            def __exit__(self, *_): return None
            def execute(self, statement): self.statement = statement
            def fetchall(self): return [{"legal_folder": 50182, "distribution_date": "2026-10-30"}]

        class Connection:
            def __init__(self): self.cursor_instance = Cursor(); self.readonly = False; self.closed = False
            def set_session(self, readonly): self.readonly = readonly
            def cursor(self, cursor_factory): return self.cursor_instance
            def close(self): self.closed = True

        connection = Connection()
        with patch.dict(os.environ, {"ENTER_POSTGRES_AID_URL": "postgresql://example/db"}), \
             patch.object(database.psycopg2, "connect", return_value=connection) as connect:
            rows = database.load_existing_cases()
        connect.assert_called_once_with("postgresql://example/db", connect_timeout=5)
        self.assertTrue(connection.readonly)
        self.assertTrue(connection.closed)
        self.assertIn('FROM "ai_deployment"."neon_reconciliation"', connection.cursor_instance.statement)
        self.assertIn('WHERE "deleted_at" IS NULL', connection.cursor_instance.statement)
        self.assertTrue(connection.cursor_instance.statement.startswith("SELECT "))
        self.assertEqual(rows[0]["legal_folder"], 50182)

    def test_missing_connection_is_explicit(self):
        with patch.dict(os.environ, {"ENTER_POSTGRES_AID_URL": ""}):
            with self.assertRaisesRegex(database.DatabaseReadError, "ENTER_POSTGRES_AID_URL"):
                database.load_existing_cases()


class DatabaseWriteTest(unittest.TestCase):
    def connection(self, fail_on=None, conflict_on_insert=False):
        class Cursor:
            def __init__(self): self.statements = []; self.last = ""
            def __enter__(self): return self
            def __exit__(self, *_): return None
            def execute(self, statement, params=None):
                self.last = statement
                self.statements.append((statement, params))
                if fail_on and fail_on in statement:
                    raise psycopg2.ProgrammingError("test database error")
            def fetchall(self):
                return [
                    {"legal_folder": 2, "case_number": "old", "case_status": "old", "deleted_at": None},
                    {"legal_folder": 3, "case_number": "missing", "deleted_at": None},
                    {"legal_folder": 4, "case_number": "same", "deleted_at": None},
                ]
            def fetchone(self):
                return None if conflict_on_insert and self.last.startswith("INSERT") else {"legal_folder": 1}

        class Connection:
            def __init__(self): self.cursor_instance = Cursor(); self.committed = False; self.rolled_back = False; self.closed = False
            def cursor(self, cursor_factory): return self.cursor_instance
            def commit(self): self.committed = True
            def rollback(self): self.rolled_back = True
            def close(self): self.closed = True

        return Connection()

    def test_insert_update_and_soft_delete_are_scoped_and_atomic(self):
        connection = self.connection()
        incoming = [
            {"legal_folder": 1, "case_number": "new", "requisitions": [], "documents": []},
            {"legal_folder": 2, "case_number": "old", "case_status": "new"},
            {"legal_folder": 4, "case_number": "same"},
        ]
        with patch.dict(os.environ, {"ENTER_POSTGRES_AID_URL": "postgresql://example/db"}), \
             patch.object(database.psycopg2, "connect", return_value=connection):
            stats = database.apply_reconciliation(incoming, [1, 2, 4], {"3": {"action": "remove", "deleted_at": "2026-10-30"}})
        self.assertEqual(stats, {"inserted": 1, "updated": 1, "removed": 1, "unchanged": 1})
        self.assertTrue(connection.committed)
        self.assertFalse(connection.rolled_back)
        self.assertTrue(connection.closed)
        statements = connection.cursor_instance.statements
        self.assertEqual(len(statements), 4)
        self.assertIn("FOR UPDATE", statements[0][0])
        self.assertIn("ON CONFLICT", statements[1][0])
        self.assertIn("DO NOTHING", statements[1][0])
        self.assertIn('"case_status" = %s, "updated_at" = now()', statements[2][0])
        self.assertNotIn('"case_number" = %s', statements[2][0])
        self.assertIn('SET "deleted_at" = %s', statements[3][0])
        self.assertNotIn('"updated_at"', statements[3][0])
        removal_at = statements[3][1][0]
        self.assertEqual(removal_at, datetime.fromisoformat("2026-10-30T18:00:00-03:00"))
        self.assertEqual(removal_at.utcoffset(), timedelta(hours=-3))

    def test_failure_rolls_back_all_writes(self):
        connection = self.connection(fail_on='SET "case_status"')
        with patch.dict(os.environ, {"ENTER_POSTGRES_AID_URL": "postgresql://example/db"}), \
             patch.object(database.psycopg2, "connect", return_value=connection):
            with self.assertRaises(database.DatabaseWriteError):
                database.apply_reconciliation(
                    [{"legal_folder": 1, "case_number": "new"}, {"legal_folder": 2, "case_number": "old", "case_status": "new"}],
                    [1, 2], {},
                )
        self.assertFalse(connection.committed)
        self.assertTrue(connection.rolled_back)
        self.assertTrue(connection.closed)

    def test_new_case_conflict_never_updates_existing_row(self):
        connection = self.connection(conflict_on_insert=True)
        with patch.dict(os.environ, {"ENTER_POSTGRES_AID_URL": "postgresql://example/db"}), \
             patch.object(database.psycopg2, "connect", return_value=connection):
            with self.assertRaisesRegex(database.DatabaseWriteError, "processe o arquivo novamente"):
                database.apply_reconciliation([{"legal_folder": 1, "case_number": "new"}], [1], {})
        self.assertFalse(connection.committed)
        self.assertTrue(connection.rolled_back)
        self.assertEqual(len(connection.cursor_instance.statements), 2)


if __name__ == "__main__":
    unittest.main()
