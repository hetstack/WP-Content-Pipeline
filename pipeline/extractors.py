"""Ekstrakcja tekstu z różnych formatów plików."""

import os
import hashlib
import logging
import yaml
import json

log = logging.getLogger(__name__)

# Mapowanie rozszerzeń na typy
TEXT_EXTENSIONS = {
    ".txt", ".md", ".log", ".sh", ".bash", ".py", ".js", ".ts",
    ".yml", ".yaml", ".json", ".toml", ".ini", ".cfg", ".conf",
    ".env", ".dockerfile", ".xml", ".csv", ".sql", ".html", ".htm",
    ".css", ".go", ".rs", ".rb", ".php", ".java", ".c", ".cpp", ".h"
}
PDF_EXTENSIONS = {".pdf"}
DOCX_EXTENSIONS = {".docx"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tiff", ".bmp"}

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB


def get_file_type(filename: str) -> str:
    """Zwraca typ pliku na podstawie rozszerzenia."""
    ext = os.path.splitext(filename)[1].lower()
    if ext in TEXT_EXTENSIONS:
        return "text"
    elif ext in PDF_EXTENSIONS:
        return "pdf"
    elif ext in DOCX_EXTENSIONS:
        return "docx"
    elif ext in IMAGE_EXTENSIONS:
        return "image"
    return "unsupported"


def file_checksum(filepath: str) -> str:
    """SHA256 pliku."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_text(filepath: str) -> str:
    """Ekstrahuj tekst z pliku — dispatch po typie."""
    file_type = get_file_type(filepath)
    if file_type == "text":
        return _extract_text_file(filepath)
    elif file_type == "pdf":
        return _extract_pdf(filepath)
    elif file_type == "docx":
        return _extract_docx(filepath)
    elif file_type == "image":
        return _extract_image_ocr(filepath)
    else:
        raise ValueError(f"Unsupported file type: {filepath}")


def _extract_text_file(filepath: str) -> str:
    """Ekstrakcja z plików tekstowych z autodetekcją kodowania."""
    for encoding in ["utf-8", "latin-1", "cp1250"]:
        try:
            with open(filepath, "r", encoding=encoding) as f:
                content = f.read()
            # Walidacja YAML/JSON
            ext = os.path.splitext(filepath)[1].lower()
            if ext in (".yml", ".yaml"):
                try:
                    parsed = yaml.safe_load(content)
                    return f"[YAML config]\n{content}"
                except yaml.YAMLError:
                    pass
            elif ext == ".json":
                try:
                    parsed = json.loads(content)
                    return f"[JSON data]\n{content}"
                except json.JSONDecodeError:
                    pass
            return content
        except UnicodeDecodeError:
            continue
    return "[Binary content - could not decode]"


def _extract_pdf(filepath: str) -> str:
    """Ekstrakcja tekstu z PDF."""
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(filepath)
        text = ""
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
        if len(text.strip()) < 50:
            # PDF może być skanem — próba OCR
            return _extract_pdf_ocr(filepath)
        return text
    except Exception as e:
        log.error(f"PDF extraction failed: {e}")
        return _extract_pdf_ocr(filepath)


def _extract_pdf_ocr(filepath: str) -> str:
    """OCR na stronach PDF (fallback)."""
    try:
        import subprocess
        import pytesseract
        from PIL import Image
        # Konwersja PDF → obrazy
        result = subprocess.run(
            ["pdftoppm", "-png", "-r", "300", filepath, "/tmp/pdf_page"],
            capture_output=True, timeout=120
        )
        text = ""
        import glob
        for img_path in sorted(glob.glob("/tmp/pdf_page-*.png")):
            img = Image.open(img_path)
            text += pytesseract.image_to_string(img, lang="pol+eng") + "\n"
            os.remove(img_path)
        return text if text.strip() else "[PDF OCR: no text extracted]"
    except Exception as e:
        log.error(f"PDF OCR failed: {e}")
        return f"[PDF extraction failed: {e}]"


def _extract_docx(filepath: str) -> str:
    """Ekstrakcja z DOCX."""
    try:
        from docx import Document
        doc = Document(filepath)
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    except Exception as e:
        log.error(f"DOCX extraction failed: {e}")
        return f"[DOCX extraction failed: {e}]"


def _extract_image_ocr(filepath: str) -> str:
    """OCR na obrazie."""
    try:
        import pytesseract
        from PIL import Image
        img = Image.open(filepath)
        text = pytesseract.image_to_string(img, lang="pol+eng")
        return text if text.strip() else "[OCR: no text found in image]"
    except Exception as e:
        log.error(f"Image OCR failed: {e}")
        return f"[Image OCR failed: {e}]"
