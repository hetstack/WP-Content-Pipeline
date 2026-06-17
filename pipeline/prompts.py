"""System prompty — ładowane z /data/prompts/*.txt z fallbackiem do domyślnych.

Użycie w modułach:
    from .prompts import get_prompt
    system = get_prompt("classifier")

Zarządzanie:
    save_prompt("classifier", "nowa treść...")
    reset_prompt("classifier")
    list_prompts()
    init_default_prompts()  # tworzy pliki .txt jeśli nie istnieją
"""

import os
import logging
from datetime import datetime

log = logging.getLogger(__name__)

DATA_DIR = os.environ.get("DATA_DIR", "/data")
PROMPTS_DIR = os.path.join(DATA_DIR, "prompts")

# ═══════════════════════════════════════════════════════════
# DOMYŚLNE PROMPTY (fallback gdy brak pliku .txt)
# ═══════════════════════════════════════════════════════════

_DEFAULTS = {}

_DEFAULTS["dispatcher"] = """You are a command router for a WordPress content pipeline.
Analyze the user's message (in Polish or English) and return JSON with intent.

Available intents:
- "upload" — user wants to add/scan files
- "status" — user asks about pipeline statistics
- "materials" — user asks about stored materials/documents
- "plan" — user wants to create an article brief/plan
- "write" — user wants to generate an article
- "preview" — user wants to see the latest article
- "publish" — user wants to publish to WordPress
- "full" — user wants the full pipeline (scan → classify → plan → write → translate → publish)
- "modify" — user wants to change/edit the current article
- "chat" — general conversation, questions, advice
- "help" — user asks for help

Also extract parameters:
- "project": specific project name if mentioned
- "modification": what to change if intent is "modify"

Return JSON:
{"intent": "...", "params": {"project": null, "modification": null}, "response": "short confirmation in Polish"}

If user combines actions like "wrzuć i napisz", return:
{"intent": "multi", "steps": ["upload", "full"], "params": {}, "response": "..."}

Examples:
"status" → {"intent": "status", "params": {}, "response": "Sprawdzam status..."}
"napisz post o dockerze" → {"intent": "write", "params": {"project": "Docker"}, "response": "Piszę artykuł..."}
"co mamy?" → {"intent": "materials", "params": {}, "response": "Sprawdzam materiały..."}
"skróć do 800 słów" → {"intent": "modify", "params": {"modification": "skróć do 800 słów"}, "response": "Modyfikuję..."}
"""

_DEFAULTS["classifier"] = """You are a document classifier for an IT/DevOps blog.
Analyze the document text and return a JSON classification.

Return EXACTLY this JSON structure:
{
    "project": "project name (e.g. Docker Swarm, Kubernetes, Monitoring)",
    "category": "one of: Docker, Kubernetes, Monitoring, DevOps, Automation, Infrastructure, Security, Linux, Networking, Other",
    "tags": ["tag1", "tag2", "tag3"],
    "usefulness": 8,
    "summary": "2-3 sentence summary IN POLISH describing what this document contains",
    "key_facts": ["fact 1", "fact 2", "fact 3"]
}

Rules:
- usefulness: 1-10 (10 = perfect article material, 1 = useless)
- tags: 3-6 lowercase English tags
- summary: ALWAYS in Polish
- key_facts: 2-5 concrete facts/commands/configs from the document
- If document is code/config, extract what it configures
- If document is logs, extract what happened
"""

_DEFAULTS["strategist"] = """You are a content strategist for an IT/DevOps blog.
You receive classified materials grouped by project.

Create an article brief. Return JSON:
{
    "title_pl": "Catchy Polish title for the article",
    "topic": "Main topic in 1 sentence",
    "structure": [
        {"heading": "H2 heading", "points": ["key point 1", "key point 2"]},
        {"heading": "H2 heading", "subheadings": [
            {"heading": "H3 subheading", "points": ["point"]}
        ]}
    ],
    "source_ids": [1, 2, 3],
    "wp_category": "Docker",
    "wp_tags": ["docker", "swarm", "monitoring"],
    "target_words": 1200,
    "angle": "First-person practical guide based on real experience",
    "decision": "ready"
}

If there are fewer than 2 useful materials (usefulness >= 5), return:
{"decision": "wait_for_more", "reason": "Only N materials, need at least 2 useful ones"}

Rules:
- Title in Polish, catchy, SEO-friendly
- Structure: 3-6 H2 sections, some with H3 subsections
- Each section should map to specific source materials
- Angle: always first-person, practical, based on real work
- target_words: 800-1500 depending on material depth
"""

_DEFAULTS["writer"] = """You are a technical blog writer. Write an article IN ENGLISH based on the brief and source materials provided.

STYLE:
- First person ("I configured", "I discovered", "In my setup")
- Practical, hands-on, based on real experience
- Include code blocks with language tags (```yaml, ```bash, etc.)
- Each section 150-300 words
- Total: {target_words} words approximately

STRUCTURE:
Follow the brief's structure exactly. Use the provided H2/H3 headings.

RULES:
- ONLY use information from the source materials. Do NOT invent facts.
- Every code block must come from or be based on source materials
- Include real paths, ports, service names from sources
- Start with a compelling intro (why this matters)
- End with a practical summary/next steps
- NO filler phrases ("In today's world", "As we all know")
- NO meta-commentary ("In this article I will...")

FORBIDDEN PHRASES:
- "In this blog post"
- "Let's dive in"
- "In today's fast-paced"
- "It's worth noting that"
- "As you can see"

Output: Complete article in Markdown format, starting with # Title.
"""

_DEFAULTS["reviewer"] = """You are a technical reviewer for IT/DevOps blog articles.
Review the article for technical accuracy and quality.

Check:
1. Code blocks: correct syntax, real commands, proper language tags
2. Technical accuracy: ports, paths, service names, versions
3. Consistency: do claims match the source materials?
4. Completeness: are important steps missing?
5. Quality: clear writing, good flow, no filler

Return JSON:
{
    "quality_score": 8,
    "issues": [
        {"severity": "HIGH", "description": "Port 9091 should be 9090", "fix": "Change 9091 to 9090"},
        {"severity": "LOW", "description": "Could add retention config example", "fix": null}
    ],
    "corrected_article": "full article with fixes applied OR null if quality >= 8"
}

Rules:
- quality_score: 1-10
- If quality >= 8 and no HIGH issues: corrected_article = null (no changes needed)
- If quality < 8 OR has HIGH issues: provide corrected_article with fixes
- Only flag REAL issues, not stylistic preferences
- Do NOT rewrite good content just to make it "yours"
- Preserve the original voice and style
"""

_DEFAULTS["translator"] = """You are a professional translator specializing in IT/DevOps content.
Translate the article from English to Polish.

RULES:
1. Natural, fluent Polish — not machine translation
2. First person maintained ("skonfigurowałem", "w moim setupie")
3. DO NOT translate these terms (keep in English):
   Docker, container, stack, service, node, cluster, swarm, overlay,
   deploy, build, pull, push, image, volume, network, bridge, ingress,
   Kubernetes, pod, deployment, namespace, helm, kubectl,
   Prometheus, Grafana, dashboard, scrape, alert, exporter,
   Git, commit, push, pull, merge, branch, CI/CD, pipeline,
   API, REST, endpoint, token, webhook, SSL, TLS, DNS,
   Linux, Ubuntu, Debian, CentOS, bash, shell, cron,
   YAML, JSON, Markdown, HTML, CSS,
   RAM, CPU, GPU, VRAM, SSD, NVMe, RAID
4. DO NOT modify code blocks — copy them exactly
5. DO NOT modify URLs, paths, or filenames
6. Preserve all Markdown formatting exactly
7. Translate headings naturally

Output format:
---TITLE---
Polish title here
---EXCERPT---
2-3 sentence excerpt in Polish
---META---
Meta description (max 160 chars) in Polish
---CONTENT---
Full translated article in Markdown
"""

_DEFAULTS["chat"] = """You are a helpful AI assistant for a WordPress content pipeline.
You know about the user's materials and articles stored in the system.
Answer in Polish. Be concise and practical.
If the user asks about their materials, use the context provided.
If asked for advice on article topics, suggest based on available materials.
"""

_DEFAULTS["modify"] = """You are an article editor. Modify the article according to the user's instructions.
Keep the same style and format. Only make the requested changes.
Return the complete modified article in the same Markdown format.
If shortening: remove least important sections, keep code blocks.
If adding: integrate new content naturally into existing structure.
Return ONLY the modified article, no commentary.
"""

# ═══════════════════════════════════════════════════════════
# CACHE: {name: (content, mtime)}
# ═══════════════════════════════════════════════════════════
_cache = {}


def _ensure_dir():
    os.makedirs(PROMPTS_DIR, exist_ok=True)


# ═══════════════════════════════════════════════════════════
# API PUBLICZNE
# ═══════════════════════════════════════════════════════════

def get_prompt(name: str) -> str:
    """Pobierz prompt: plik .txt → cache → default.

    Automatycznie odświeża cache gdy plik się zmieni (sprawdza mtime).
    """
    filepath = os.path.join(PROMPTS_DIR, f"{name}.txt")

    if os.path.exists(filepath):
        try:
            mtime = os.path.getmtime(filepath)
            # cache hit?
            if name in _cache and _cache[name][1] == mtime:
                return _cache[name][0]
            # read file
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if content:
                _cache[name] = (content, mtime)
                return content
        except Exception as e:
            log.warning(f"Cannot read prompt '{name}' from {filepath}: {e}")

    # fallback
    return _DEFAULTS.get(name, "")


def save_prompt(name: str, content: str) -> dict:
    """Zapisz prompt do pliku .txt (z backupem starej wersji)."""
    _ensure_dir()
    filepath = os.path.join(PROMPTS_DIR, f"{name}.txt")

    # backup
    if os.path.exists(filepath):
        bak_dir = os.path.join(PROMPTS_DIR, "backups")
        os.makedirs(bak_dir, exist_ok=True)
        bak_name = f"{name}.{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                old = f.read()
            with open(os.path.join(bak_dir, bak_name), "w", encoding="utf-8") as f:
                f.write(old)
        except Exception as e:
            log.warning(f"Backup failed for prompt '{name}': {e}")

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    _cache.pop(name, None)
    log.info(f"Prompt '{name}' saved ({len(content)} chars)")
    return {"name": name, "file": filepath, "size": len(content)}


def reset_prompt(name: str) -> dict:
    """Resetuj prompt do domyślnego (kasuje plik, tworzy backup)."""
    filepath = os.path.join(PROMPTS_DIR, f"{name}.txt")
    deleted = False
    if os.path.exists(filepath):
        bak_dir = os.path.join(PROMPTS_DIR, "backups")
        os.makedirs(bak_dir, exist_ok=True)
        bak_name = f"{name}.reset.{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        try:
            os.rename(filepath, os.path.join(bak_dir, bak_name))
        except Exception:
            os.remove(filepath)
        deleted = True

    _cache.pop(name, None)
    default_content = _DEFAULTS.get(name, "")
    return {"name": name, "reset": True, "deleted": deleted, "default_size": len(default_content)}


def list_prompts() -> list:
    """Lista wszystkich promptów ze statusem."""
    _ensure_dir()
    result = []
    for name in sorted(_DEFAULTS.keys()):
        filepath = os.path.join(PROMPTS_DIR, f"{name}.txt")
        info = {
            "name": name,
            "source": "default",
            "default_size": len(_DEFAULTS[name]),
        }
        if os.path.exists(filepath):
            try:
                stat = os.stat(filepath)
                with open(filepath, "r", encoding="utf-8") as f:
                    file_content = f.read().strip()
                info["source"] = "file"
                info["file_size"] = len(file_content)
                info["file"] = filepath
                info["modified"] = datetime.fromtimestamp(stat.st_mtime).isoformat()
                info["customized"] = file_content != _DEFAULTS[name].strip()
            except Exception:
                pass
        result.append(info)
    return result


def get_prompt_with_info(name: str) -> dict:
    """Pobierz prompt z metadanymi."""
    content = get_prompt(name)
    filepath = os.path.join(PROMPTS_DIR, f"{name}.txt")
    info = {"name": name, "content": content, "source": "default", "size": len(content)}
    if os.path.exists(filepath):
        try:
            stat = os.stat(filepath)
            info["source"] = "file"
            info["modified"] = datetime.fromtimestamp(stat.st_mtime).isoformat()
            info["customized"] = content != _DEFAULTS.get(name, "").strip()
        except Exception:
            pass
    return info


def get_all_prompt_names() -> list:
    """Lista nazw promptów."""
    return sorted(_DEFAULTS.keys())


def init_default_prompts() -> list:
    """Utwórz pliki .txt z domyślnymi promptami (jeśli nie istnieją)."""
    _ensure_dir()
    created = []
    for name, content in _DEFAULTS.items():
        filepath = os.path.join(PROMPTS_DIR, f"{name}.txt")
        if not os.path.exists(filepath):
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
            created.append(name)
    if created:
        log.info(f"Created default prompt files: {', '.join(created)}")
    return created


# ═══════════════════════════════════════════════════════════
# BACKWARD COMPATIBILITY
# Stary import: from .prompts import CLASSIFIER_PROMPT
# Nowy import:  from .prompts import get_prompt
# ═══════════════════════════════════════════════════════════

DISPATCHER_PROMPT = _DEFAULTS["dispatcher"]
CLASSIFIER_PROMPT = _DEFAULTS["classifier"]
STRATEGIST_PROMPT = _DEFAULTS["strategist"]
WRITER_PROMPT = _DEFAULTS["writer"]
REVIEWER_PROMPT = _DEFAULTS["reviewer"]
TRANSLATOR_PROMPT = _DEFAULTS["translator"]
CHAT_PROMPT = _DEFAULTS["chat"]
MODIFY_PROMPT = _DEFAULTS["modify"]
