"""M5: Reviewer — korekta techniczna (Mistral-7B)."""

import os
import json
import time
import logging
from .database import get_db
from .ollama_client import OllamaClient
from .prompts import get_prompt

log = logging.getLogger(__name__)


class Reviewer:
    def __init__(self, ollama: OllamaClient = None, model: str = None, **kwargs):
        self.ollama = ollama or OllamaClient()
        self.model = model or os.environ.get("MODEL_REVIEWER", "mistral:7b-instruct")

    def review(self, article_id: int = None, code_only: bool = False, 
               quality_threshold: int = 7):
        """Generator — przegląd techniczny artykułu EN."""
        yield {"event": "phase", "phase": "M5",
               "message": f"Korekta techniczna (model: {self.model})"}

        db = get_db()
        if article_id:
            article = db.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
        else:
            article = db.execute(
                "SELECT * FROM articles WHERE status = 'draft_en' ORDER BY created_at DESC LIMIT 1"
            ).fetchone()

        if not article:
            yield {"event": "error", "message": "Brak artykułu EN do korekty"}
            return

        brief = db.execute("SELECT * FROM briefs WHERE id = ?", (article["brief_id"],)).fetchone()
        sources_text = ""
        if brief:
            source_ids = json.loads(brief["source_ids"])
            for sid in source_ids[:3]:
                doc = db.execute("SELECT filename, content FROM documents WHERE id = ?", (sid,)).fetchone()
                if doc:
                    sources_text += f"\n--- {doc['filename']} ---\n{doc['content'][:1500]}\n"

        self.ollama.swap_model(self.model)
        yield {"event": "model_loaded", "model": self.model}

        review_focus = ""
        if code_only:
            review_focus = "\nFOCUS ONLY on code blocks: syntax, correctness, completeness. Ignore prose style."

        prompt = f"""Article to review:
{article['content_en']}

Source materials for verification:
{sources_text}{review_focus}"""

        start = time.time()
        yield {"event": "heartbeat", "phase": "M5", "message": "Reviewing..."}

        result = self.ollama.generate_json(
            model=self.model,
            system=get_prompt("reviewer"),
            prompt=prompt,
            temperature=0.3,
            num_predict=6000,
            num_ctx=16384
        )

        elapsed = int(time.time() - start)
        score = result.get("quality_score", 7)
        issues = result.get("issues", [])
        corrected = result.get("corrected_article")

        if score < quality_threshold and not corrected:
            yield {"event": "heartbeat", "phase": "M5", 
                   "message": f"Jakość {score}/{quality_threshold} - wymuszam poprawki..."}

        content_final = corrected if corrected else article["content_en"]
        db.execute(
            """UPDATE articles
               SET content_en_rev = ?, review_score = ?, review_notes = ?,
                   reviewer_model = ?, status = 'reviewed'
               WHERE id = ?""",
            (content_final, score, json.dumps(issues, ensure_ascii=False),
             self.model, article["id"])
        )
        db.commit()

        high_issues = [i for i in issues if i.get("severity") == "HIGH"]
        low_issues = [i for i in issues if i.get("severity") != "HIGH"]

        yield {
            "event": "reviewed",
            "quality_score": score,
            "high_issues": len(high_issues),
            "low_issues": len(low_issues),
            "issues": issues[:5],
            "corrected": corrected is not None,
            "elapsed_seconds": elapsed
        }
        yield {"event": "done", "phase": "M5",
               "message": f"Jakość: {score}/10, {len(issues)} uwag, {elapsed}s"}
