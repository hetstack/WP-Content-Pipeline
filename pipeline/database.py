"""SQLite — schemat i połączenie."""

import sqlite3
import os
import threading

_local = threading.local()
DB_PATH = os.path.join(os.environ.get("DATA_DIR", "/data"), "db", "pipeline.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    content TEXT,
    file_type TEXT,
    file_size INTEGER,
    char_count INTEGER,
    checksum TEXT UNIQUE,
    status TEXT DEFAULT 'new',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS classifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL,
    project TEXT,
    category TEXT,
    tags TEXT,
    usefulness INTEGER,
    summary TEXT,
    key_facts TEXT,
    model_used TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (document_id) REFERENCES documents(id)
);

CREATE TABLE IF NOT EXISTS briefs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    topic TEXT,
    structure TEXT,
    source_ids TEXT,
    wp_category TEXT,
    wp_tags TEXT,
    target_words INTEGER DEFAULT 1200,
    status TEXT DEFAULT 'created',
    model_used TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brief_id,
    title_en TEXT,
    content_en TEXT,
    content_en_rev TEXT,
    review_score INTEGER,
    review_notes TEXT,
    title_pl TEXT,
    content_pl TEXT,
    excerpt_pl TEXT,
    meta_desc_pl TEXT,
    wp_category TEXT,
    wp_tags TEXT,
    wp_post_id INTEGER,
    wp_post_url TEXT,
    writer_model TEXT,
    reviewer_model TEXT,
    translator_model TEXT,
    status TEXT DEFAULT 'draft_en',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (brief_id) REFERENCES briefs(id)
);

CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);
CREATE INDEX IF NOT EXISTS idx_documents_checksum ON documents(checksum);
CREATE INDEX IF NOT EXISTS idx_classifications_project ON classifications(project);
CREATE INDEX IF NOT EXISTS idx_briefs_status ON briefs(status);
CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(status);
"""


def get_db() -> sqlite3.Connection:
    """Thread-safe SQLite connection."""
    if not hasattr(_local, "conn") or _local.conn is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        _local.conn = sqlite3.connect(DB_PATH, timeout=30)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


def init_db():
    """Tworzenie tabel i indeksów."""
    db = get_db()
    db.executescript(SCHEMA)
    db.commit()


def get_stats() -> dict:
    """Statystyki pipeline."""
    db = get_db()
    docs = db.execute("SELECT status, COUNT(*) as c FROM documents GROUP BY status").fetchall()
    briefs = db.execute("SELECT status, COUNT(*) as c FROM briefs GROUP BY status").fetchall()
    articles = db.execute("SELECT status, COUNT(*) as c FROM articles GROUP BY status").fetchall()
    return {
        "documents": {
            "total": sum(r["c"] for r in docs),
            "by_status": {r["status"]: r["c"] for r in docs}
        },
        "briefs": {
            "total": sum(r["c"] for r in briefs),
            "by_status": {r["status"]: r["c"] for r in briefs}
        },
        "articles": {
            "total": sum(r["c"] for r in articles),
            "by_status": {r["status"]: r["c"] for r in articles}
        }
    }
