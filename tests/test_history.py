import io
import json
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch

import openpyxl

from app import server
from app.consolidation import load_mapping


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        data = Path(self.directory.name)
        self.patches = [
            patch.object(server, "DATA", data),
            patch.object(server, "UPLOADS", data / "uploads"),
            patch.object(server, "PENDING", data / "pending"),
            patch.object(server, "MOCK_FILE", data / "mock_cases.json"),
            patch.object(server, "HISTORY_FILE", data / "history.json"),
            patch.object(server, "RUNS", {}),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        server.ensure_data()

    def handler(self, route, body=None):
        handler = server.Handler.__new__(server.Handler)
        handler.path = route
        handler.body_json = lambda: body or {}
        def send_json(value, status=200):
            handler.response = (value, status)
            return handler.response
        handler.send_json = send_json
        return handler

    def upload(self, valid=True):
        if valid:
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
            contents = stream.getvalue()
        else:
            contents = b"PKnot-a-workbook"
        boundary = b"history-test-boundary"
        payload = b"--" + boundary + b'\r\nContent-Disposition: form-data; name="file"; filename="relatorio.xlsx"\r\nContent-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n' + contents + b"\r\n--" + boundary + b"--\r\n"
        handler = self.handler("/api/process")
        handler.headers = Message()
        handler.headers["Content-Length"] = str(len(payload))
        handler.headers["Content-Type"] = "multipart/form-data; boundary=" + boundary.decode()
        handler.rfile = io.BytesIO(payload)
        handler.do_POST()
        return handler.response

    def history(self):
        return self.handler("/api/history").do_GET()[0]

    def test_only_completed_runs_enter_history(self):
        valid, status = self.upload()
        self.assertEqual(status, 200)
        self.assertEqual(self.history(), [])
        self.assertEqual(len(list(server.PENDING.iterdir())), 1)
        self.assertEqual(list(server.UPLOADS.iterdir()), [])

        cancelled, status = self.handler("/api/cancel", {"run_id": valid["run_id"]}).do_POST()
        self.assertEqual((cancelled, status), ({"ok": True}, 200))
        self.assertEqual(self.history(), [])
        self.assertEqual(list(server.PENDING.iterdir()), [])

        invalid, status = self.upload(valid=False)
        self.assertEqual(status, 400)
        self.assertIn("formato esperado", invalid["error"])
        self.assertEqual(self.history(), [])
        self.assertEqual(list(server.PENDING.iterdir()), [])

        valid, status = self.upload()
        self.assertEqual(status, 200)
        handler = self.handler("/api/finish", {"run_id": valid["run_id"], "decisions": {}})
        handler.do_POST()
        result, status = handler.response
        self.assertEqual(status, 200)
        self.assertEqual(set(result["stats"]), {"inserted", "updated", "removed", "unchanged", "invalid"})
        self.assertEqual(len(self.history()), 1)
        self.assertEqual(self.history()[0]["status"], "completed")
        self.assertEqual(self.history()[0]["summary"], {"cases": valid["cases"], **result["stats"]})
        self.assertEqual(list(server.PENDING.iterdir()), [])
        self.assertEqual(len(list(server.UPLOADS.iterdir())), 1)

    def test_legacy_unfinished_entries_are_removed_on_startup(self):
        (server.PENDING / "interrupted.xlsx").write_bytes(b"old upload")
        server.atomic_json(server.HISTORY_FILE, [
            {"id": "old", "status": "cancelled"},
            {"id": "done", "status": "completed", "summary": {"cases": 10, "new": 2, "updated": 3, "removed": 1, "invalid": 1}},
        ])
        server.ensure_data()
        self.assertEqual(list(server.PENDING.iterdir()), [])
        self.assertEqual([item["id"] for item in self.history()], ["done"])
        self.assertEqual([item["id"] for item in json.loads(server.HISTORY_FILE.read_text())], ["done"])
        self.assertEqual(self.history()[0]["summary"], {"cases": 10, "inserted": 2, "updated": 3, "removed": 1, "unchanged": 5, "invalid": 1})


if __name__ == "__main__":
    unittest.main()
