"""M2: Classifier — klasyfikacja dokumentów Qwen-3B."""

import os
import json
import logging
from .database import get_db
from .ollama_client import OllamaClient
from .prompts import get_prompt

log = logging.getLogger(__name__)


class Classifier:
    def __init__(self, ollama: OllamaClient = None, model: str = None, **kwargs):
        self.ollama = ollama or OllamaClient()
        self.model = model or os.environ.get("MODEL_CLASSIFIER", "qwen2.5:3b")

    def classify_all(self, document_id: int = None, project: str = None, force: bool = False):
        """Generator — klasyfikuj dokumenty, yield events."""
        db = get_db()
        
        if document_id:
            if force:
                docs = db.execute(
                    "SELECT id, filename, content, file_type FROM documents WHERE id = ?",
                    (document_id,)
                ).fetchall()
            else:
                docs = db.execute(
                    "SELECT id, filename, content, file_type FROM documents WHERE id = ? AND status = 'new'",
                    (document_id,)
                ).fetchall()
        elif project:
            docs = db.execute(
                """SELECT d.id, d.filename, d.content, d.file_type 
                   FROM documents d
                   JOIN classifications c ON d.id = c.document_id
                   WHERE c.project LIKE ? AND (d.status = 'new' OR ? = 1)""",
                (f"%{project}%", 1 if force else 0)
            ).fetchall()
        else:
            docs = db.execute(
                "SELECT id, filename, content, file_type FROM documents WHERE status = 'new'"
            ).fetchall()

        if not docs:
            yield {"event": "classify_empty", "message": "Brak dokumentów do klasyfikacji"}
            return

        yield {"event": "phase", "phase": "M2",
               "message": f"Klasyfikacja {len(docs)} dokumentów (model: {self.model})"}

        self.ollama.swap_model(self.model)
        yield {"event": "model_loaded", "model": self.model}

        classified = 0
        for doc in docs:
            try:
                result = self._classify_one(doc, force)
                classified += 1
                yield {"event": "classified", **result}
            except Exception as e:
                log.error(f"Classification failed for {doc['filename']}: {e}")
                yield {"event": "error", "filename": doc["filename"], "error": str(e)}

        yield {
            "event": "done", "phase": "M2",
            "message": f"Sklasyfikowano: {classified}/{len(docs)}"
        }

    def _classify_one(self, doc, force: bool = False) -> dict:
        """Klasyfikuj jeden dokument."""
        db = get_db()
        
        if force:
            db.execute("DELETE FROM classifications WHERE document_id = ?", (doc["id"],))
            db.commit()
        
        content = doc["content"][:6000]
        prompt = f"Filename: {doc['filename']}\nFile type: {doc['file_type']}\n\nContent:\n{content}"

        result = self.ollama.generate_json(
            model=self.model,
            system=get_prompt("classifier"),
            prompt=prompt,
            temperature=0.3,
            num_predict=1024
        )

        if "error" in result:
            raise ValueError(f"JSON parse error: {result.get('raw', '')[:200]}")

        project = result.get("project", "Unknown")
        category = result.get("category", "Other")
        tags = result.get("tags", [])
        usefulness = min(10, max(1, result.get("usefulness", 5)))
        summary = result.get("summary", "Brak opisu")
        key_facts = result.get("key_facts", [])

        db.execute(
            """INSERT INTO classifications
               (document_id, project, category, tags, usefulness, summary, key_facts, model_used)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (doc["id"], project, category, json.dumps(tags), usefulness,
             summary, json.dumps(key_facts), self.model)
        )
        db.execute("UPDATE documents SET status = 'classified' WHERE id = ?", (doc["id"],))
        db.commit()

        return {
            "filename": doc["filename"],
            "project": project,
            "category": category,
            "usefulness": usefulness,
            "summary": summary
        }
