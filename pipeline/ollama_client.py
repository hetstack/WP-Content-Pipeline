"""Klient Ollama — ładowanie modeli, generowanie, swap VRAM."""

import os
import json
import time
import logging
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://10.2.10.20:11434")


class OllamaClient:
    def __init__(self, base_url: str = None, **kwargs):
        self.base_url = (base_url or OLLAMA_URL).rstrip("/")
        self.current_model = None

    def health_check(self) -> bool:
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return r.status_code == 200
        except Exception:
            return False

    def get_loaded_models(self) -> list[str]:
        try:
            r = requests.get(f"{self.base_url}/api/ps", timeout=5)
            if r.status_code == 200:
                return [m["name"] for m in r.json().get("models", [])]
        except Exception:
            pass
        return []

    def unload_all(self):
        """Wymuś zwolnienie VRAM — keep_alive=0 dla każdego załadowanego modelu."""
        loaded = self.get_loaded_models()
        for model in loaded:
            try:
                requests.post(
                    f"{self.base_url}/api/generate",
                    json={"model": model, "prompt": "", "keep_alive": 0},
                    timeout=30
                )
                log.info(f"Unloaded: {model}")
            except Exception as e:
                log.warning(f"Unload failed for {model}: {e}")
        if loaded:
            time.sleep(3)  # poczekaj na dealokację VRAM
        self.current_model = None

    def swap_model(self, model_name: str):
        """Zwolnij aktualny model → załaduj nowy."""
        if self.current_model == model_name:
            log.info(f"Model {model_name} already loaded, skip swap")
            return
        log.info(f"Swap: {self.current_model} → {model_name}")
        self.unload_all()
        # Warmup — załaduj model do VRAM
        self._warmup(model_name)
        self.current_model = model_name

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    def _warmup(self, model_name: str):
        """Załaduj model do VRAM przez minimalne generowanie."""
        r = requests.post(
            f"{self.base_url}/api/generate",
            json={
                "model": model_name,
                "prompt": "Hi",
                "options": {"num_predict": 1},
                "keep_alive": "10m"
            },
            timeout=120
        )
        r.raise_for_status()
        log.info(f"Model {model_name} loaded")

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=2, max=8))
    def generate(self, model: str, system: str, prompt: str,
                 num_predict: int = 4096, temperature: float = 0.7,
                 num_ctx: int = 8192, stream: bool = False) -> str:
        """Generuj odpowiedź z Ollama."""
        payload = {
            "model": model,
            "system": system,
            "prompt": prompt,
            "stream": stream,
            "options": {
                "num_predict": num_predict,
                "temperature": temperature,
                "num_ctx": num_ctx
            },
            "keep_alive": "10m"
        }
        if stream:
            return self._generate_stream(payload)

        r = requests.post(
            f"{self.base_url}/api/generate",
            json=payload,
            timeout=600
        )
        r.raise_for_status()
        return r.json().get("response", "")

    def _generate_stream(self, payload: dict):
        """Generator — yield chunków tekstu z Ollama stream."""
        r = requests.post(
            f"{self.base_url}/api/generate",
            json=payload,
            timeout=600,
            stream=True
        )
        r.raise_for_status()
        full_response = ""
        for line in r.iter_lines():
            if line:
                data = json.loads(line)
                chunk = data.get("response", "")
                full_response += chunk
                yield {
                    "chunk": chunk,
                    "done": data.get("done", False),
                    "total_tokens": data.get("eval_count", 0)
                }
        return full_response

    def generate_json(self, model: str, system: str, prompt: str,
                      num_predict: int = 2048, temperature: float = 0.3,
                      num_ctx: int = 8192) -> dict:
        """Generuj JSON z Ollama (format=json)."""
        payload = {
            "model": model,
            "system": system,
            "prompt": prompt,
            "format": "json",
            "stream": False,
            "options": {
                "num_predict": num_predict,
                "temperature": temperature,
                "num_ctx": num_ctx
            },
            "keep_alive": "10m"
        }
        r = requests.post(
            f"{self.base_url}/api/generate",
            json=payload,
            timeout=600
        )
        r.raise_for_status()
        text = r.json().get("response", "{}")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Spróbuj wyciągnąć JSON z tekstu
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(text[start:end])
            log.error(f"Cannot parse JSON from: {text[:200]}")
            return {"error": "invalid_json", "raw": text[:500]}

    def chat(self, model: str, messages: list[dict],
             num_predict: int = 2048, temperature: float = 0.7) -> str:
        """Chat completion z Ollama."""
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {
                "num_predict": num_predict,
                "temperature": temperature
            },
            "keep_alive": "10m"
        }
        r = requests.post(
            f"{self.base_url}/api/chat",
            json=payload,
            timeout=600
        )
        r.raise_for_status()
        return r.json().get("message", {}).get("content", "")
