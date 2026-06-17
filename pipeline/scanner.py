"""M1: Scanner — ekstrakcja tekstu z plików w /data/inbox/."""

import os
import shutil
import logging
from datetime import datetime
from .database import get_db
from .extractors import get_file_type, file_checksum, extract_text, MAX_FILE_SIZE

log = logging.getLogger(__name__)


class Scanner:
    def __init__(self, data_dir: str = None, **kwargs):
        self.data_dir = data_dir or os.environ.get("DATA_DIR", "/data")
        self.inbox = os.path.join(self.data_dir, "inbox")
        self.processing = os.path.join(self.data_dir, "processing")
        self.processed = os.path.join(self.data_dir, "processed")
        self.failed = os.path.join(self.data_dir, "failed")

    def scan_all(self):
        """Generator — skanuj inbox, yield events NDJSON."""
        os.makedirs(self.inbox, exist_ok=True)
        files = [f for f in os.listdir(self.inbox)
                 if os.path.isfile(os.path.join(self.inbox, f))]

        if not files:
            yield {"event": "scan_empty", "message": "Inbox pusty — brak plików do skanowania"}
            return

        yield {"event": "phase", "phase": "M1", "message": f"Skanowanie {len(files)} plików"}
        scanned = 0
        skipped = 0

        for filename in sorted(files):
            filepath = os.path.join(self.inbox, filename)
            try:
                result = self._process_file(filepath, filename)
                if result["status"] == "scanned":
                    scanned += 1
                    yield {"event": "scanned", **result}
                else:
                    skipped += 1
                    yield {"event": "skipped", **result}
            except Exception as e:
                skipped += 1
                log.error(f"Error scanning {filename}: {e}")
                self._move_to_failed(filepath, "extraction_error")
                yield {"event": "error", "filename": filename, "error": str(e)}

        yield {
            "event": "done", "phase": "M1",
            "message": f"Zeskanowano: {scanned}, pominięto: {skipped}"
        }

    def _process_file(self, filepath: str, filename: str) -> dict:
        """Przetwórz jeden plik: typ → rozmiar → duplikat → ekstrakcja."""
        file_type = get_file_type(filename)
        file_size = os.path.getsize(filepath)

        # Za duży
        if file_size > MAX_FILE_SIZE:
            self._move_to_failed(filepath, "too_large")
            return {"status": "skipped", "filename": filename,
                    "reason": f"Za duży: {file_size / 1024 / 1024:.1f} MB"}

        # Nieobsługiwany format
        if file_type == "unsupported":
            self._move_to_failed(filepath, "unsupported")
            return {"status": "skipped", "filename": filename,
                    "reason": f"Nieobsługiwany format"}

        # Duplikat (SHA256)
        checksum = file_checksum(filepath)
        db = get_db()
        existing = db.execute(
            "SELECT id, filename FROM documents WHERE checksum = ?", (checksum,)
        ).fetchone()
        if existing:
            os.remove(filepath)
            return {"status": "skipped", "filename": filename,
                    "reason": f"Duplikat: {existing['filename']}"}

        # Ekstrakcja tekstu
        content = extract_text(filepath)
        char_count = len(content)

        if char_count < 10:
            self._move_to_failed(filepath, "extraction_error")
            return {"status": "skipped", "filename": filename,
                    "reason": "Za mało treści (<10 znaków)"}

        # Zapis do SQLite
        db.execute(
            """INSERT INTO documents (filename, content, file_type, file_size, char_count, checksum, status)
               VALUES (?, ?, ?, ?, ?, ?, 'new')""",
            (filename, content, file_type, file_size, char_count, checksum)
        )
        db.commit()

        # Przeniesienie do processed/YYYY-MM/
        self._move_to_processed(filepath)

        return {
            "status": "scanned", "filename": filename,
            "file_type": file_type, "char_count": char_count,
            "file_size": file_size
        }

    def _move_to_processed(self, filepath: str):
        dest_dir = os.path.join(self.processed, datetime.now().strftime("%Y-%m"))
        os.makedirs(dest_dir, exist_ok=True)
        shutil.move(filepath, os.path.join(dest_dir, os.path.basename(filepath)))

    def _move_to_failed(self, filepath: str, reason: str):
        dest_dir = os.path.join(self.failed, reason)
        os.makedirs(dest_dir, exist_ok=True)
        shutil.move(filepath, os.path.join(dest_dir, os.path.basename(filepath)))

    def save_uploaded_files(self, files: list[dict]) -> list[dict]:
        """Zapisz pliki przesłane przez API do inbox."""
        import base64
        results = []
        for f in files:
            filename = f.get("filename", "unknown")
            content_b64 = f.get("content", "")
            try:
                data = base64.b64decode(content_b64)
                filepath = os.path.join(self.inbox, filename)
                with open(filepath, "wb") as fh:
                    fh.write(data)
                results.append({"filename": filename, "size": len(data), "status": "saved"})
            except Exception as e:
                results.append({"filename": filename, "status": "error", "error": str(e)})
        return results
