import io
import json
import os
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch

import openpyxl

from app import database, server
from app.consolidation import load_mapping


class ReadOnlyWorkflowTest(unittest.TestCase):
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

    def test_preview_and_finish_do_not_write_files(self):
        result, status = self.upload()
        self.assertEqual(status, 200)
        self.assertEqual(len(result["comparison"]["new"]), 1)
        self.assertEqual(len(result["comparison"]["missing"]), 1)
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])

        finish = self.handler("/api/finish", {"run_id": result["run_id"], "decisions": {
            "70001": {"action": "remove", "deleted_at": "2026-10-30"}
        }})
        finish.do_POST()
        summary, status = finish.response
        self.assertEqual(status, 200)
        self.assertEqual(summary["stats"]["inserted"], 1)
        self.assertEqual(summary["stats"]["removed"], 1)
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])
        self.assertEqual(self.handler("/api/history").do_GET(), ([], 200))

    def test_cancel_discards_in_memory_preview(self):
        result, status = self.upload()
        self.assertEqual(status, 200)
        self.assertIn(result["run_id"], server.RUNS)
        self.assertEqual(self.handler("/api/cancel", {"run_id": result["run_id"]}).do_POST(), ({"ok": True}, 200))
        self.assertNotIn(result["run_id"], server.RUNS)
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


if __name__ == "__main__":
    unittest.main()
