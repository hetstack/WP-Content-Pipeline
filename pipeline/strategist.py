"""M3: Strategist — analiza materiałów i brief artykułu (Qwen-7B)."""

import os
import json
import logging
from .database import get_db
from .ollama_client import OllamaClient
from .prompts import get_prompt

log = logging.getLogger(__name__)


class Strategist:
    def __init__(self, ollama: OllamaClient = None, model: str = None, **kwargs):
        self.ollama = ollama or OllamaClient()
        self.model = model or os.environ.get("MODEL_STRATEGIST", "qwen2.5:7b-instruct")

    def plan(self, project: str = None, target_words: int = None, 
             check_only: bool = False, series: bool = False):
        """Generator — utwórz brief na podstawie materiałów, yield events."""
        yield {"event": "phase", "phase": "M3",
               "message": f"Analiza materiałów (model: {self.model})"}

        db = get_db()

        if project:
            rows = db.execute(
                """SELECT d.id, d.filename, d.content, c.project, c.category,
                          c.usefulness, c.summary, c.key_facts
                   FROM documents d
                   JOIN classifications c ON d.id = c.document_id
                   WHERE c.project LIKE ? AND d.status = 'classified'
                   ORDER BY c.usefulness DESC""",
                (f"%{project}%",)
            ).fetchall()
        else:
            best = db.execute(
                """SELECT c.project, AVG(c.usefulness) as avg_u, COUNT(*) as cnt
                   FROM classifications c
                   JOIN documents d ON d.id = c.document_id
                   WHERE d.status = 'classified'
                   GROUP BY c.project
                   HAVING cnt >= 2
                   ORDER BY avg_u DESC LIMIT 1"""
            ).fetchone()

            if not best:
                yield {"event": "wait_for_more",
                       "message": "Za mało materiałów — potrzebuję min. 2 sklasyfikowane dokumenty z jednego projektu"}
                return

            project = best["project"]
            rows = db.execute(
                """SELECT d.id, d.filename, d.content, c.project, c.category,
                          c.usefulness, c.summary, c.key_facts
                   FROM documents d
                   JOIN classifications c ON d.id = c.document_id
                   WHERE c.project = ? AND d.status = 'classified'
                   ORDER BY c.usefulness DESC""",
                (project,)
            ).fetchall()

        useful = [r for r in rows if r["usefulness"] >= 5]
        if len(useful) < 2:
            yield {"event": "wait_for_more",
                   "message": f"Projekt '{project}': {len(useful)} przydatnych materiałów, potrzebuję min. 2"}
            return

        if check_only:
            yield {"event": "materials_ready",
                   "project": project,
                   "count": len(useful),
                   "message": f"Gotowe materiały: {len(useful)} z projektu '{project}'"}
            return

        yield {"event": "progress", "message": f"Analizuję {len(useful)} materiałów z projektu: {project}"}

        materials_text = ""
        source_ids = []
        for r in useful[:8]:
            materials_text += f"\n--- Document #{r['id']}: {r['filename']} ---\n"
            materials_text += f"Category: {r['category']}, Usefulness: {r['usefulness']}/10\n"
            materials_text += f"Summary: {r['summary']}\n"
            materials_text += f"Key facts: {r['key_facts']}\n"
            materials_text += f"Content (first 2000 chars):\n{r['content'][:2000]}\n"
            source_ids.append(r["id"])

        self.ollama.swap_model(self.model)
        yield {"event": "model_loaded", "model": self.model}

        extra_instructions = ""
        if target_words:
            extra_instructions = f"\nTarget article length: approximately {target_words} words."
        if series:
            extra_instructions += "\nThis should be planned as part of a series - focus on one specific aspect."

        prompt = f"Project: {project}\nNumber of materials: {len(useful)}{extra_instructions}\n\n{materials_text}"
        result = self.ollama.generate_json(
            model=self.model,
            system=get_prompt("strategist"),
            prompt=prompt,
            temperature=0.4,
            num_predict=2048,
            num_ctx=16384
        )

        if result.get("decision") == "wait_for_more":
            yield {"event": "wait_for_more", "message": result.get("reason", "Za mało materiałów")}
            return

        final_target_words = target_words or result.get("target_words", 1200)

        brief_data = {
            "title": result.get("title_pl", f"Artykuł o {project}"),
            "topic": result.get("topic", project),
            "structure": json.dumps(result.get("structure", []), ensure_ascii=False),
            "source_ids": json.dumps(source_ids),
            "wp_category": result.get("wp_category", "DevOps"),
            "wp_tags": json.dumps(result.get("wp_tags", [project.lower()])),
            "target_words": final_target_words,
            "model_used": self.model
        }

        db.execute(
            """INSERT INTO briefs (title, topic, structure, source_ids, wp_category, wp_tags, target_words, model_used, status)
               VALUES (:title, :topic, :structure, :source_ids, :wp_category, :wp_tags, :target_words, :model_used, 'created')""",
            brief_data
        )
        db.commit()

        for sid in source_ids:
            db.execute("UPDATE documents SET status = 'used' WHERE id = ?", (sid,))
        db.commit()

        yield {
            "event": "brief_created",
            "title": brief_data["title"],
            "topic": brief_data["topic"],
            "structure": result.get("structure", []),
            "source_count": len(source_ids),
            "target_words": final_target_words,
            "wp_category": brief_data["wp_category"]
        }
        yield {"event": "done", "phase": "M3", "message": f"Brief utworzony: {brief_data['title']}"}
