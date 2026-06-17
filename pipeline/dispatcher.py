"""M0: Dispatcher — routing intencji + czat konwersacyjny."""

import os
import json
import logging
from .database import get_db
from .ollama_client import OllamaClient
from .prompts import get_prompt

log = logging.getLogger(__name__)


class Dispatcher:
    def __init__(self, ollama: OllamaClient = None, model: str = None, **kwargs):
        self.ollama = ollama or OllamaClient()
        self.model = model or os.environ.get("MODEL_DISPATCHER", "qwen2.5:3b")

    def dispatch(self, message: str, has_files: bool = False,
                 history: list = None) -> dict:
        """Zrozum intencję użytkownika → JSON."""
        quick = self._quick_match(message, has_files)
        if quick:
            return quick

        self.ollama.swap_model(self.model)
        context = f"User message: {message}\nHas files attached: {has_files}"
        if history:
            context += f"\nRecent history: {json.dumps(history[-3:], ensure_ascii=False)}"

        result = self.ollama.generate_json(
            model=self.model,
            system=get_prompt("dispatcher"),
            prompt=context,
            temperature=0.2,
            num_predict=512
        )

        intent = result.get("intent", "chat")
        valid_intents = {"upload", "status", "materials", "plan", "write",
                         "preview", "publish", "full", "modify", "chat",
                         "help", "multi"}
        if intent not in valid_intents:
            intent = "chat"
            result["intent"] = "chat"

        return result

    def chat(self, message: str, context: str = "") -> str:
        """Konwersacja po polsku z kontekstem materiałów."""
        self.ollama.swap_model(self.model)

        db = get_db()
        stats_ctx = ""
        try:
            docs = db.execute(
                """SELECT d.filename, c.project, c.summary, c.usefulness
                   FROM documents d LEFT JOIN classifications c ON d.id = c.document_id
                   WHERE d.status IN ('classified', 'used')
                   ORDER BY d.created_at DESC LIMIT 10"""
            ).fetchall()
            if docs:
                stats_ctx = "\n\nMateriały w systemie:\n"
                for d in docs:
                    stats_ctx += f"- {d['filename']}: {d['project']} ({d['usefulness']}/10) — {d['summary']}\n"
        except Exception:
            pass

        messages = [
            {"role": "system", "content": get_prompt("chat") + stats_ctx + (f"\nDodatkowy kontekst: {context}" if context else "")},
            {"role": "user", "content": message}
        ]
        return self.ollama.chat(model=self.model, messages=messages, temperature=0.7)

    def modify(self, message: str) -> dict:
        """Modyfikuj ostatni artykuł PL."""
        db = get_db()
        article = db.execute(
            "SELECT * FROM articles WHERE content_pl IS NOT NULL ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if not article:
            return {"error": "Brak artykułu do modyfikacji"}

        self.ollama.swap_model(self.model)
        prompt = f"""Artykuł do modyfikacji:
{article['content_pl']}

Instrukcja modyfikacji: {message}"""

        modified = self.ollama.generate(
            model=self.model,
            system=get_prompt("modify"),
            prompt=prompt,
            temperature=0.5,
            num_predict=4096
        )

        db.execute(
            "UPDATE articles SET content_pl = ? WHERE id = ?",
            (modified, article["id"])
        )
        db.commit()

        word_count = len(modified.split())
        return {
            "status": "modified",
            "word_count": word_count,
            "article_id": article["id"]
        }

    def _quick_match(self, message: str, has_files: bool) -> dict | None:
        """Regex fallback — szybkie komendy bez GPU."""
        msg = message.strip().lower()

        mapping = {
            "status": "status", "stan": "status", "ile": "status",
            "pomoc": "help", "help": "help", "?": "help",
            "podgląd": "preview", "podglad": "preview", "pokaż artykuł": "preview",
            "opublikuj": "publish", "publikuj": "publish",
            "materiały": "materials", "materialy": "materials", "pliki": "materials",
        }

        for keyword, intent in mapping.items():
            if msg.startswith(keyword) or msg == keyword:
                return {"intent": intent, "params": {}, "response": "OK"}

        if any(w in msg for w in ["napisz", "generuj", "artykuł", "post"]):
            return {"intent": "write", "params": {}, "response": "Piszę artykuł..."}

        if any(w in msg for w in ["zaplanuj", "brief", "plan"]):
            return {"intent": "plan", "params": {}, "response": "Planuję artykuł..."}

        if any(w in msg for w in ["pełny", "pelny", "full", "zrób wszystko", "run"]):
            return {"intent": "full", "params": {}, "response": "Uruchamiam pełny pipeline..."}

        if any(w in msg for w in ["skanuj", "scan", "wrzuć", "dodaj"]) or has_files:
            return {"intent": "upload", "params": {}, "response": "Skanuję pliki..."}

        return None
