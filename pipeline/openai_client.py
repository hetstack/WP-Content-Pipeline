"""OpenAI-compatible LLM Client — dla LiteLLM, OpenRouter, itp.
   Interfejs identyczny z OllamaClient — drop-in replacement."""

import json
import logging
import requests

log = logging.getLogger(__name__)


class OpenAIClient:
    """Klient LLM kompatybilny z OpenAI API.
    
    Ma ten sam interfejs co OllamaClient, więc moduły (Writer, Classifier, etc.)
    mogą go używać bez żadnych zmian.
    """

    def __init__(self, base_url="http://litellm:4000", api_key="", **kwargs):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.current_model = None

    def _headers(self):
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    # ── Kompatybilność z OllamaClient ──

    def health_check(self) -> bool:
        try:
            r = requests.get(f"{self.base_url}/v1/models",
                             headers=self._headers(), timeout=5)
            return r.status_code == 200
        except Exception:
            return False

    def get_loaded_models(self) -> list:
        """API nie ma koncepcji załadowanych modeli."""
        return []

    def unload_all(self):
        """No-op — API nie wymaga zwalniania VRAM."""
        self.current_model = None

    def swap_model(self, model_name: str):
        """No-op — API nie wymaga ładowania modeli."""
        self.current_model = model_name
        log.info(f"OpenAI track: using model {model_name}")

    # ── Generowanie ──

    def generate(self, model: str, system: str, prompt: str,
                 num_predict: int = 4096, temperature: float = 0.7,
                 num_ctx: int = 8192, stream: bool = False, **kwargs):
        """Generuj odpowiedź — kompatybilne z OllamaClient.generate()"""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt}
        ]
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": num_predict,
            "stream": stream
        }

        if stream:
            return self._stream_generate(payload)

        r = requests.post(f"{self.base_url}/v1/chat/completions",
                          json=payload, headers=self._headers(), timeout=600)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    def _stream_generate(self, payload):
        """Generator chunków — format identyczny z OllamaClient."""
        r = requests.post(f"{self.base_url}/v1/chat/completions",
                          json=payload, headers=self._headers(),
                          timeout=600, stream=True)
        r.raise_for_status()

        total_tokens = 0
        for line in r.iter_lines():
            if not line:
                continue
            text = line.decode("utf-8")
            if not text.startswith("data: "):
                continue
            data_str = text[6:].strip()
            if data_str == "[DONE]":
                yield {"chunk": "", "done": True, "total_tokens": total_tokens}
                return
            try:
                data = json.loads(data_str)
                delta = data.get("choices", [{}])[0].get("delta", {})
                chunk = delta.get("content", "")
                if chunk:
                    total_tokens += len(chunk.split())
                    yield {"chunk": chunk, "done": False, "total_tokens": total_tokens}
            except (json.JSONDecodeError, IndexError, KeyError):
                pass

    def generate_json(self, model: str, system: str, prompt: str,
                      num_predict: int = 2048, temperature: float = 0.3,
                      num_ctx: int = 8192, **kwargs) -> dict:
        """Generuj JSON — kompatybilne z OllamaClient.generate_json()"""
        json_system = system + "\n\nIMPORTANT: Respond with valid JSON only."

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": json_system},
                {"role": "user", "content": prompt}
            ],
            "temperature": temperature,
            "max_tokens": num_predict,
        }

        # Niektóre modele obsługują response_format
        try:
            payload["response_format"] = {"type": "json_object"}
            r = requests.post(f"{self.base_url}/v1/chat/completions",
                              json=payload, headers=self._headers(), timeout=600)
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
        except Exception:
            # Fallback bez response_format
            del payload["response_format"]
            r = requests.post(f"{self.base_url}/v1/chat/completions",
                              json=payload, headers=self._headers(), timeout=600)
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start:end])
                except json.JSONDecodeError:
                    pass
            log.error(f"OpenAI JSON parse failed: {text[:200]}")
            return {"error": "invalid_json", "raw": text[:500]}

    def chat(self, model: str, messages: list,
             num_predict: int = 2048, temperature: float = 0.7, **kwargs) -> str:
        """Chat completion — kompatybilne z OllamaClient.chat()"""
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": num_predict,
        }
        r = requests.post(f"{self.base_url}/v1/chat/completions",
                          json=payload, headers=self._headers(), timeout=600)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
