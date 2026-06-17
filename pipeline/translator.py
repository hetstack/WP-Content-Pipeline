"""M6: Translator — tłumaczenie EN→PL z live streamingiem."""

import os
import time
import logging
from .database import get_db
from .prompts import get_prompt

log = logging.getLogger(__name__)


class Translator:
    def __init__(self, ollama=None, model: str = None, **kwargs):
        self.ollama = ollama
        self.model = model or os.environ.get("MODEL_TRANSLATOR", "qwen2.5:7b-instruct")

    def translate(self, article_id: int = None, style: str = None,
                  force: bool = False, live_stream: bool = True):
        """Generator — tłumaczenie EN→PL z live chunkami."""
        yield {"event": "phase", "phase": "M6",
               "message": f"Tłumaczenie EN→PL (model: {self.model})"}

        db = get_db()
        if article_id:
            article = db.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
        else:
            article = db.execute(
                "SELECT * FROM articles WHERE status = 'reviewed' ORDER BY created_at DESC LIMIT 1"
            ).fetchone()

        if not article:
            yield {"event": "error", "message": "Brak artykułu do tłumaczenia"}
            return

        if article["content_pl"] and not force:
            yield {"event": "skipped", "message": "Artykuł już przetłumaczony. Użyj --force."}
            return

        content_en = article["content_en_rev"] or article["content_en"]

        self.ollama.swap_model(self.model)
        yield {"event": "model_loaded", "model": self.model}

        style_instruction = ""
        if style == "formal":
            style_instruction = "\nUse formal Polish."
        elif style == "informal":
            style_instruction = "\nUse casual, friendly Polish."

        prompt = f"Translate this article to Polish:{style_instruction}\n\n{content_en}"

        start = time.time()
        full_text = ""
        last_heartbeat = time.time()

        # ═══ LIVE STREAMING ═══
        yield {"event": "stream_start", "phase": "M6", "message": "Tłumaczenie..."}

        for chunk_data in self.ollama.generate(
            model=self.model, system=get_prompt("TRANSLATOR_PROMPT"), prompt=prompt,
            num_predict=6000, temperature=0.5, num_ctx=16384,
            stream=True
        ):
            chunk = chunk_data.get("chunk", "")
            full_text += chunk

            if chunk and live_stream:
                yield {"event": "stream_chunk", "chunk": chunk, "phase": "M6"}

            now = time.time()
            if now - last_heartbeat >= 15:
                elapsed = int(now - start)
                pct = min(95, int(len(full_text) / max(len(content_en), 1) * 100))
                yield {"event": "progress", "phase": "M6", "percent": pct, "elapsed": elapsed}
                last_heartbeat = now

            if chunk_data.get("done"):
                break

        yield {"event": "stream_end", "phase": "M6"}

        elapsed = int(time.time() - start)
        title_pl, excerpt_pl, meta_desc_pl, content_pl = self._parse_translation(full_text, article)

        db.execute(
            """UPDATE articles SET title_pl=?, content_pl=?, excerpt_pl=?, meta_desc_pl=?,
               translator_model=?, status='translated' WHERE id=?""",
            (title_pl, content_pl, excerpt_pl, meta_desc_pl, self.model, article["id"])
        )
        db.commit()

        word_count = len(content_pl.split())
        yield {"event": "translated", "title_pl": title_pl,
               "word_count": word_count, "elapsed_seconds": elapsed}
        yield {"event": "done", "phase": "M6",
               "message": f"Tłumaczenie PL: {word_count} słów, {elapsed}s"}

    def _parse_translation(self, text: str, article) -> tuple:
        title_pl = ""
        excerpt_pl = ""
        meta_desc_pl = ""
        content_pl = text

        if "---TITLE---" in text:
            parts = text.split("---")
            sections = {}
            current_key = None
            for part in parts:
                part = part.strip()
                if part.upper() in ("TITLE", "EXCERPT", "META", "CONTENT"):
                    current_key = part.upper()
                elif current_key:
                    sections[current_key] = part
                    current_key = None
            title_pl = sections.get("TITLE", "").strip()
            excerpt_pl = sections.get("EXCERPT", "").strip()
            meta_desc_pl = sections.get("META", "").strip()[:160]
            content_pl = sections.get("CONTENT", text).strip()

        if not title_pl:
            lines = content_pl.strip().split("\n")
            if lines and lines[0].startswith("#"):
                title_pl = lines[0].lstrip("# ").strip()
            else:
                title_pl = article["title_en"]
        if not excerpt_pl:
            sentences = content_pl.replace("\n", " ").split(".")
            excerpt_pl = ". ".join(sentences[:2]).strip() + "."
        if not meta_desc_pl:
            meta_desc_pl = excerpt_pl[:160]

        return title_pl, excerpt_pl, meta_desc_pl, content_pl
