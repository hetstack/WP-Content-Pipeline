"""M4: Writer — pisanie artykułu EN z live streamingiem."""

import os
import json
import time
import logging
from .database import get_db
from .prompts import get_prompt

log = logging.getLogger(__name__)


class Writer:
    def __init__(self, ollama=None, model: str = None, **kwargs):
        self.ollama = ollama
        self.model = model or os.environ.get("MODEL_WRITER", "llama3.1:8b")

    def write(self, brief_id: int = None, article_id: int = None,
              target_words: int = None, rewrite: bool = False,
              live_stream: bool = True):
        """Generator — napisz artykuł EN, yield events + live chunks."""
        yield {"event": "phase", "phase": "M4",
               "message": f"Pisanie artykułu EN (model: {self.model})"}

        db = get_db()

        if article_id and rewrite:
            article = db.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
            if not article:
                yield {"event": "error", "message": f"Artykuł #{article_id} nie istnieje"}
                return
            brief_id = article["brief_id"]

        if brief_id:
            brief = db.execute("SELECT * FROM briefs WHERE id = ?", (brief_id,)).fetchone()
        else:
            brief = db.execute(
                "SELECT * FROM briefs WHERE status = 'created' ORDER BY created_at DESC LIMIT 1"
            ).fetchone()

        if not brief:
            yield {"event": "error", "message": "Brak briefu — najpierw zaplanuj artykuł (M3)"}
            return

        source_ids = json.loads(brief["source_ids"])
        sources_text = ""
        for sid in source_ids:
            doc = db.execute("SELECT filename, content FROM documents WHERE id = ?", (sid,)).fetchone()
            if doc:
                sources_text += f"\n--- Source: {doc['filename']} ---\n{doc['content'][:3000]}\n"

        structure = json.loads(brief["structure"])
        final_target_words = target_words or brief["target_words"] or 1200

        self.ollama.swap_model(self.model)
        yield {"event": "model_loaded", "model": self.model}

        system = get_prompt("WRITER_PROMPT").replace("{target_words}", str(final_target_words))
        prompt = f"""Article Brief:
Title: {brief['title']}
Topic: {brief['topic']}
Target: ~{final_target_words} words
Structure: {json.dumps(structure, indent=2)}

Source Materials:
{sources_text}

Write the complete article now."""

        start = time.time()
        full_text = ""
        token_count = 0
        last_heartbeat = time.time()

        # ═══ LIVE STREAMING ═══
        yield {"event": "stream_start", "phase": "M4", "message": "Rozpoczynam pisanie..."}

        for chunk_data in self.ollama.generate(
            model=self.model, system=system, prompt=prompt,
            num_predict=6000, temperature=0.7, num_ctx=16384,
            stream=True
        ):
            chunk = chunk_data.get("chunk", "")
            full_text += chunk
            token_count = chunk_data.get("total_tokens", token_count)

            # Live chunk — wysyłany na bieżąco do frontendu
            if chunk and live_stream:
                yield {"event": "stream_chunk", "chunk": chunk, "phase": "M4"}

            # Progress heartbeat (rzadziej)
            now = time.time()
            if now - last_heartbeat >= 15:
                elapsed = int(now - start)
                words = len(full_text.split())
                pct = min(95, int(words / final_target_words * 100))
                yield {
                    "event": "progress", "phase": "M4",
                    "percent": pct, "tokens": token_count,
                    "words": words, "elapsed": elapsed
                }
                last_heartbeat = now

            if chunk_data.get("done"):
                break

        yield {"event": "stream_end", "phase": "M4"}

        elapsed = int(time.time() - start)
        word_count = len(full_text.split())

        title_en = brief["title"]
        lines = full_text.strip().split("\n")
        if lines and lines[0].startswith("#"):
            title_en = lines[0].lstrip("# ").strip()

        if rewrite and article_id:
            db.execute(
                "UPDATE articles SET content_en = ?, writer_model = ?, status = 'draft_en' WHERE id = ?",
                (full_text, self.model, article_id)
            )
        else:
            db.execute(
                "INSERT INTO articles (brief_id, title_en, content_en, writer_model, status) VALUES (?, ?, ?, ?, 'draft_en')",
                (brief["id"], title_en, full_text, self.model)
            )
        db.execute("UPDATE briefs SET status = 'writing' WHERE id = ?", (brief["id"],))
        db.commit()

        yield {
            "event": "written",
            "title_en": title_en,
            "word_count": word_count,
            "elapsed_seconds": elapsed
        }
        yield {"event": "done", "phase": "M4",
               "message": f"Artykuł EN: {word_count} słów, {elapsed}s"}
