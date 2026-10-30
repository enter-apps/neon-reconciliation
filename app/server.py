"""A single Railway-friendly server for the local reconciliation workflow."""

from __future__ import annotations

import cgi
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
from pathlib import Path
import threading
from urllib.parse import quote, urlparse
from uuid import uuid4

from .consolidation import load_mapping, transform
from .consolidation.cleaners import clean_cnj, is_blank
from .consolidation.transform import read_source
from .reconcile import apply, normalize, preview


ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "app" / "static"
DATA = Path(os.environ.get("DATA_DIR", str(ROOT / "data"))).resolve()
UPLOADS = DATA / "uploads"
PENDING = DATA / "pending"
MOCK_FILE = DATA / "mock_cases.json"
HISTORY_FILE = DATA / "history.json"
MAX_UPLOAD = 25 * 1024 * 1024
REPORT_FORMAT_ERROR = "O arquivo não corresponde ao formato esperado de relatório customizado do Projuris"
LOCK = threading.RLock()
RUNS: dict[str, dict] = {}


def ensure_data():
    UPLOADS.mkdir(parents=True, exist_ok=True)
    PENDING.mkdir(parents=True, exist_ok=True)
    for path in PENDING.iterdir():
        if path.is_file():
            path.unlink()
    if not MOCK_FILE.exists():
        sample = json.loads((ROOT / "app" / "mock_cases.example.json").read_text(encoding="utf-8"))
        atomic_json(MOCK_FILE, sample)
    if not HISTORY_FILE.exists():
        atomic_json(HISTORY_FILE, [])
    else:
        history = read_json(HISTORY_FILE)
        completed = [item for item in history if item.get("status") == "completed"]
        changed = len(completed) != len(history)
        for item in completed:
            summary = item.get("summary")
            if summary:
                before = dict(summary)
                summary["inserted"] = summary.get("inserted", summary.get("new", 0))
                summary["unchanged"] = summary.get(
                    "unchanged", max(0, summary.get("cases", 0) - summary["inserted"] - summary.get("updated", 0))
                )
                summary.pop("new", None)
                changed |= summary != before
        if changed:
            atomic_json(HISTORY_FILE, completed)


def atomic_json(path: Path, value):
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def completed_history() -> list[dict]:
    return [item for item in read_json(HISTORY_FILE) if item.get("status") == "completed"]


def archived_upload(run_id: str) -> tuple[Path, str] | None:
    entry = next((item for item in completed_history() if item.get("id") == run_id), None)
    if entry is None:
        return None
    stored_name = entry.get("stored_name", "")
    if not stored_name or Path(stored_name).name != stored_name:
        return None
    path = UPLOADS / stored_name
    if not path.is_file() or path.resolve().parent != UPLOADS.resolve():
        return None
    return path, entry.get("name") or stored_name


def invalid_cases(frame, mapping) -> list[dict]:
    cnj_field = next(field for field in mapping.fields if field.clean == "cnj")
    folder_field = next(field for field in mapping.fields if field.target == mapping.group_by)
    seen = set()
    invalid = []
    for _, row in frame.iterrows():
        raw = row[cnj_field.column]
        if clean_cnj(raw):
            continue
        folder = normalize(row[folder_field.column])
        value = None if is_blank(raw) else str(raw)
        key = (str(folder), value)
        if key not in seen:
            seen.add(key)
            invalid.append({"legal_folder": folder, "case_number": value})
    return invalid


class Handler(BaseHTTPRequestHandler):
    def send_json(self, value, status=HTTPStatus.OK):
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def send_file(self, path: Path, content_type: str, download_name: str | None = None):
        payload = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        if download_name:
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(download_name, safe='')}")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def body_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length > 1024 * 1024:
            raise ValueError("Requisição muito grande")
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        route = urlparse(self.path).path
        if route in {"/", "/index.html"}:
            return self.send_file(STATIC / "index.html", "text/html; charset=utf-8")
        if route == "/api/history":
            with LOCK:
                return self.send_json(completed_history())
        if route.startswith("/api/history/") and route.endswith("/download"):
            run_id = route[len("/api/history/"):-len("/download")]
            with LOCK:
                archived = archived_upload(run_id)
            if archived:
                path, name = archived
                return self.send_file(path, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", name)
            return self.send_json({"error": "Arquivo desta reconciliação não encontrado"}, HTTPStatus.NOT_FOUND)
        if route.startswith("/static/"):
            name = route.removeprefix("/static/")
            if "/" in name or name.startswith("."):
                return self.send_json({"error": "Arquivo não encontrado"}, HTTPStatus.NOT_FOUND)
            path = STATIC / name
            if not path.is_file():
                return self.send_json({"error": "Arquivo não encontrado"}, HTTPStatus.NOT_FOUND)
            kind = {".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml", ".jpg": "image/jpeg", ".ttf": "font/ttf"}.get(path.suffix, "application/octet-stream")
            return self.send_file(path, kind)
        self.send_json({"error": "Página não encontrada"}, HTTPStatus.NOT_FOUND)

    def do_POST(self):
        try:
            route = urlparse(self.path).path
            if route == "/api/process":
                return self.process()
            if route == "/api/finish":
                return self.finish_run()
            if route == "/api/cancel":
                run_id = self.body_json().get("run_id")
                with LOCK:
                    run = RUNS.pop(run_id, None)
                    if run:
                        run["path"].unlink(missing_ok=True)
                return self.send_json({"ok": True})
            self.send_json({"error": "Ação não encontrada"}, HTTPStatus.NOT_FOUND)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        except Exception:
            logging.exception("Unexpected server error")
            self.send_json({"error": "Não foi possível processar o arquivo, verifique o formato e tente novamente"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def process(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_UPLOAD:
            raise ValueError("Selecione um arquivo Excel de até 25 MB")
        content_type = self.headers.get("Content-Type", "")
        if not content_type.startswith("multipart/form-data"):
            raise ValueError("Envie o arquivo como formulário")
        form = cgi.FieldStorage(fp=self.rfile, headers=self.headers, environ={"REQUEST_METHOD": "POST", "CONTENT_TYPE": content_type, "CONTENT_LENGTH": str(length)})
        if "file" not in form or not form["file"].filename:
            raise ValueError("Selecione um arquivo Excel")
        upload = form["file"]
        original_name = Path(upload.filename).name
        if Path(original_name).suffix.lower() != ".xlsx":
            raise ValueError(REPORT_FORMAT_ERROR)
        contents = upload.file.read(MAX_UPLOAD + 1)
        if len(contents) > MAX_UPLOAD:
            raise ValueError("O arquivo ultrapassa 25 MB")
        if not contents.startswith(b"PK"):
            raise ValueError(REPORT_FORMAT_ERROR)
        timestamp = datetime.now(timezone.utc)
        run_id = uuid4().hex
        stored_name = f"{timestamp.strftime('%Y%m%dT%H%M%S%fZ')}_{run_id[:8]}_{original_name}"
        path = PENDING / stored_name
        path.write_bytes(contents)
        history_item = {"id": run_id, "name": original_name, "stored_name": stored_name, "timestamp": timestamp.isoformat()}
        try:
            mapping = load_mapping()
            source = read_source(path, mapping)
            frame = transform(path, mapping, source=source)
            cases = [normalize(item) for item in frame.to_dict("records")]
            report_keys = [int(key) for key in frame.attrs["report_keys"] if not is_blank(key)]
            invalid = invalid_cases(source, mapping)
            with LOCK:
                comparison = preview(cases, report_keys, read_json(MOCK_FILE))
                RUNS[run_id] = {"cases": cases, "report_keys": report_keys, "invalid": invalid, "path": path, "file": history_item}
        except Exception as error:
            path.unlink(missing_ok=True)
            logging.warning("Arquivo não corresponde ao relatório customizado esperado: %s", error)
            raise ValueError(REPORT_FORMAT_ERROR) from error
        self.send_json({"run_id": run_id, "cases": len(cases), "invalid": invalid, "comparison": comparison})

    def finish_run(self):
        body = self.body_json()
        run_id = body.get("run_id")
        decisions = body.get("decisions", {})
        with LOCK:
            run = RUNS.get(run_id)
            if run is None:
                raise ValueError("Esta reconciliação não está mais ativa, envie o arquivo novamente")
            existing = read_json(MOCK_FILE)
            comparison = preview(run["cases"], run["report_keys"], existing)
            next_cases, stats = apply(run["cases"], existing, comparison, decisions)
            history = read_json(HISTORY_FILE)
            summary = {"cases": len(run["cases"]), **stats, "invalid": len(run["invalid"])}
            entry = {**run["file"], "status": "completed", "summary": summary}
            history.insert(0, entry)
            archived_path = UPLOADS / run["path"].name
            run["path"].replace(archived_path)
            try:
                atomic_json(MOCK_FILE, next_cases)
                atomic_json(HISTORY_FILE, history)
            except Exception:
                atomic_json(MOCK_FILE, existing)
                archived_path.replace(run["path"])
                raise
            del RUNS[run_id]
        self.send_json({"stats": {**stats, "invalid": len(run["invalid"])}})


def main():
    ensure_data()
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Neon Reconciliation em http://localhost:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
