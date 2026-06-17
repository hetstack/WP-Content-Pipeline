# 🤖 WP Content Pipeline
An advanced, end-to-end AI content factory designed to automate technical blog writing (IT/DevOps). This pipeline takes raw files (PDFs, logs, scripts, docs), extracts the text, classifies it, and orchestrates multiple LLMs to plan, write, technically review, translate, and publish high-quality articles directly to WordPress.

### 🌟 Key Features
* **8-Stage Pipeline (M0-M7):** Dispatching, Scanning/OCR, Classifying, Strategizing, Writing, Technical Reviewing, Translating, and Publishing.
* **Multi-Track AI Management:** Run models locally via Ollama (with smart VRAM/GPU locking) and automatically fall back to cloud APIs (OpenAI/LiteLLM) if needed.
* **Live Streaming:** Watch the AI write and translate articles in real-time via NDJSON streaming.
* **Dynamic Prompt Engineering:** Edit and version system prompts on the fly via a built-in web editor.
* **Anti-Bot WordPress Integration:** Includes a custom WordPress session handler that can bypass JS AES anti-bot challenges (common on free hosting).

---


# 🤖 WP Content Pipeline
Zaawansowany, zautomatyzowany silnik AI (end-to-end) stworzony do generowania i publikowania technicznych artykułów na bloga (IT/DevOps). System pobiera surowe pliki (PDF, logi, skrypty, dokumentację), wyciąga z nich tekst, klasyfikuje, a następnie przy użyciu wielu modeli LLM planuje, pisze, weryfikuje, tłumaczy i publikuje gotowy artykuł bezpośrednio w WordPressie.

### 🌟 Najważniejsze funkcje
* **Proces 8-etapowy (M0-M7):** Od routing komend, skanowanie/OCR, klasyfikację, planowanie, pisanie, korektę techniczną, tłumaczenie, aż po publikację.
* **Zarządzanie Multi-Track AI:** Uruchamiaj modele lokalnie przez Ollama (z inteligentnym zarządzaniem pamięcią VRAM) i automatycznie przełączaj się na chmurowe API (OpenAI/LiteLLM) w razie awarii lub przeciążenia.
* **Live Streaming:** Obserwuj, jak AI pisze i tłumaczy artykuł na żywo, słowo po słowie (strumieniowanie NDJSON).
* **Inżynieria Promptów w locie:** Edytuj prompty systemowe przez wbudowany edytor WWW z automatycznym backupem historii zmian.
* **Integracja WordPress (Anti-Bot):** Własna klasa sesji potrafiąca rozwiązać zabezpieczenia JavaScript (AES) blokujące dostęp do API na darmowych hostingach.
