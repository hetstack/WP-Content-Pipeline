"""WP Content Pipeline Worker — Flask API server v0.23.0
   Multi-Track: Local GPU + API (LiteLLM) with failover.
   Prompts management. Live streaming."""

import os
import json
import time
import logging
import shutil
import threading
from datetime import datetime
from flask import Flask, request, Response, jsonify

from pipeline.database import init_db, get_db, get_stats
from pipeline.ollama_client import OllamaClient
from pipeline.openai_client import OpenAIClient
from pipeline.scanner import Scanner
from pipeline.classifier import Classifier
from pipeline.dispatcher import Dispatcher
from pipeline.strategist import Strategist
from pipeline.writer import Writer
from pipeline.reviewer import Reviewer
from pipeline.translator import Translator
from pipeline.publisher import Publisher
from pipeline.prompts import (
    get_prompt, save_prompt, reset_prompt,
    list_prompts as list_prompts_fn,
    init_default_prompts, get_prompt_with_info,
    get_all_prompt_names
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://llm-swarm_ollama:11434")
DATA_DIR = os.environ.get("DATA_DIR", "/data")
PROMPTS_DIR = os.path.join(DATA_DIR, "prompts")


# ═══════════════════════════════════════════
# TRACK MANAGER
# ═══════════════════════════════════════════

class TrackManager:
    """Zarządza śladami (Track 1-4) i przydziela klientów LLM."""

    def __init__(self):
        self.gpu_lock = threading.Lock()
        self.tracks = {}
        self.active_jobs = {}
        self.multi_track = False
        self._init_default()

    def _init_default(self):
        self.tracks[1] = {
            "enabled": True,
            "type": "local",
            "client": OllamaClient(),
            "models": {
                "dispatcher": os.environ.get("MODEL_DISPATCHER", "qwen2.5:3b"),
                "classifier": os.environ.get("MODEL_CLASSIFIER", "qwen2.5:3b"),
                "strategist": os.environ.get("MODEL_STRATEGIST", "qwen2.5:7b-instruct"),
                "writer": os.environ.get("MODEL_WRITER", "llama3.1:8b"),
                "reviewer": os.environ.get("MODEL_REVIEWER", "mistral:7b-instruct"),
                "translator": os.environ.get("MODEL_TRANSLATOR", "qwen2.5:7b-instruct"),
                "chat": os.environ.get("MODEL_CHAT", "qwen2.5:3b"),
            }
        }

    def configure(self, config: dict):
        try:
            self.multi_track = config.get("multi_track", False)
            
            # Logowanie otrzymanej konfiguracji
            log.info(f"TrackManager.configure() received: {config}")
            
            # Wsparcie dla enabled_tracks (nowe API)
            if "enabled_tracks" in config:
                enabled_tracks = config["enabled_tracks"]
                if isinstance(enabled_tracks, list):
                    # Nowy format: lista enabled tracków
                    for tid in enabled_tracks:
                        if tid in [1, 2, 3, 4]:
                            tc = config.get(f"track{tid}", {})
                            if tc.get("enabled", False):
                                self.tracks[tid] = {
                                    "enabled": True,
                                    "type": "api",
                                    "client": OpenAIClient(
                                        base_url=tc.get("url", "http://litellm:4000"),
                                        api_key=tc.get("api_key", "")
                                    ),
                                    "models": tc.get("models", {})
                                }
                            else:
                                if tid in self.tracks:
                                    self.tracks[tid]["enabled"] = False
                elif isinstance(enabled_tracks, dict):
                    # Stary format: track1, track2, track3, track4
                    for track_key, track_config in enabled_tracks.items():
                        if track_key.startswith("track") and track_key.replace("track", "").isdigit():
                            tid = int(track_key.replace("track", ""))
                            tc = track_config
                            if tc.get("enabled", False):
                                self.tracks[tid] = {
                                    "enabled": True,
                                    "type": "api",
                                    "client": OpenAIClient(
                                        base_url=tc.get("url", "http://litellm:4000"),
                                        api_key=tc.get("api_key", "")
                                    ),
                                    "models": tc.get("models", {})
                                }
                            else:
                                if tid in self.tracks:
                                    self.tracks[tid]["enabled"] = False
            
            # Oryginalna obsługa track1, track2, track3, track4
            if "track1" in config:
                t1 = config["track1"]
                if t1.get("enabled", True):
                    self.tracks[1]["enabled"] = True
                    url = t1.get("url", OLLAMA_URL)
                    self.tracks[1]["client"] = OllamaClient(base_url=url)
                    if t1.get("models"):
                        self.tracks[1]["models"].update(t1["models"])
                else:
                    self.tracks[1]["enabled"] = False
                    
            for tid in [2, 3, 4]:
                key = f"track{tid}"
                if key in config:
                    tc = config[key]
                    if tc.get("enabled", False):
                        self.tracks[tid] = {
                            "enabled": True,
                            "type": "api",
                            "client": OpenAIClient(
                                base_url=tc.get("url", "http://litellm:4000"),
                                api_key=tc.get("api_key", "")
                            ),
                            "models": tc.get("models", {})
                        }
                    else:
                        if tid in self.tracks:
                            self.tracks[tid]["enabled"] = False
                            
            log.info(f"TrackManager configured: multi={self.multi_track}, "
                     f"active=[{','.join(str(t) for t,d in self.tracks.items() if d.get('enabled'))}]")
        except Exception as e:
            log.error(f"TrackManager.configure() error: {e}")
            raise

        log.info(f"TrackManager: multi={self.multi_track}, "
                 f"active=[{','.join(str(t) for t,d in self.tracks.items() if d.get('enabled'))}]")

    def get_client(self, module: str, prefer_api: bool = False):
        if not self.multi_track:
            t = self.tracks.get(1, {})
            return t["client"], t["models"].get(module, "qwen2.5:3b"), 1

        order = [2, 3, 4, 1] if prefer_api else [1, 2, 3, 4]

        for tid in order:
            t = self.tracks.get(tid)
            if not t or not t.get("enabled"):
                continue
            if t["type"] == "local":
                if self.gpu_lock.locked() and len(order) > 1:
                    log.info(f"Track {tid} GPU zajęte, próbuję następny")
                    continue
            if t["type"] == "api":
                try:
                    if not t["client"].health_check():
                        log.warning(f"Track {tid} API niedostępne")
                        continue
                except Exception:
                    continue
            model = t["models"].get(module, "")
            if not model:
                continue
            return t["client"], model, tid

        t = self.tracks.get(1, {})
        if t.get("enabled"):
            return t["client"], t["models"].get(module, "qwen2.5:3b"), 1
        raise Exception("Brak dostępnych śladów!")

    def set_active(self, track_id, phase, model):
        self.active_jobs[track_id] = {
            "phase": phase, "model": model,
            "started": datetime.now().isoformat(), "progress": 0
        }

    def clear_active(self, track_id):
        self.active_jobs.pop(track_id, None)

    def get_status(self):
        result = {"multi_track": self.multi_track, "tracks": {}}
        for tid, t in self.tracks.items():
            info = {
                "enabled": t.get("enabled", False),
                "type": t.get("type", "?"),
                "models": t.get("models", {}),
            }
            if t["type"] == "local":
                info["gpu_locked"] = self.gpu_lock.locked()
            info["active"] = self.active_jobs.get(tid)
            try:
                info["healthy"] = t["client"].health_check() if t.get("enabled") else False
            except Exception:
                info["healthy"] = False
            result["tracks"][tid] = info
        return result


track_mgr = TrackManager()
ollama = track_mgr.tracks[1]["client"]
MODEL_CONFIG = track_mgr.tracks[1]["models"]

PIPELINE_STATE = {
    "running": False, "stop_requested": False,
    "current_phase": None, "history": [], "active_track": None
}
TRANSLATION_EXCEPTIONS = set(["Docker", "Kubernetes", "Prometheus", "Grafana",
    "Linux", "Ubuntu", "API", "REST", "JSON", "YAML", "Git", "CI/CD", "DevOps"])


def _get_model(data, key, default_key=None):
    if data and isinstance(data, dict):
        val = data.get(key)
        if val:
            return val
    return MODEL_CONFIG.get(default_key or key, "qwen2.5:3b")


def _add_history(action, details=None):
    entry = {"timestamp": datetime.now().isoformat(), "action": action, "details": details or {}}
    PIPELINE_STATE["history"].insert(0, entry)
    PIPELINE_STATE["history"] = PIPELINE_STATE["history"][:100]


def ndjson_stream(generator, phase=None):
    def generate():
        PIPELINE_STATE["running"] = True
        PIPELINE_STATE["current_phase"] = phase
        try:
            for event in generator:
                if PIPELINE_STATE["stop_requested"]:
                    yield json.dumps({"event": "stopped", "message": "Pipeline zatrzymany"}) + "\n"
                    break
                yield json.dumps(event, ensure_ascii=False) + "\n"
        except Exception as e:
            log.error(f"Stream error: {e}")
            yield json.dumps({"event": "error", "message": str(e)}) + "\n"
        finally:
            PIPELINE_STATE["running"] = False
            PIPELINE_STATE["current_phase"] = None
            PIPELINE_STATE["stop_requested"] = False
            try:
                ollama.unload_all()
            except Exception:
                pass
    return Response(generate(), mimetype="application/x-ndjson")


# ═══════════════════════════════════════════
# HEALTH & TRACKS
# ═══════════════════════════════════════════

@app.route("/health", methods=["GET"])
def health():
    ollama_ok = ollama.health_check()
    db_ok = True
    try:
        get_db()
    except Exception:
        db_ok = False
    return jsonify({
        "status": "ok" if (ollama_ok and db_ok) else "degraded",
        "ollama": ollama_ok, "database": db_ok,
        "ollama_url": ollama.base_url,
        "version": "0.23.0",
        "multi_track": track_mgr.multi_track,
        "pipeline_running": PIPELINE_STATE["running"],
        "current_phase": PIPELINE_STATE["current_phase"]
    })


@app.route("/set-tracks", methods=["POST"])
def set_tracks():
    data = request.json or {}
    track_mgr.configure(data)
    _add_history("set_tracks", {"multi_track": data.get("multi_track")})
    return jsonify({"status": "ok", "tracks": track_mgr.get_status()})


@app.route("/tracks", methods=["GET"])
def get_tracks():
    return jsonify(track_mgr.get_status())


@app.route("/set-models", methods=["POST"])
def set_models():
    data = request.json or {}
    updated = []
    for key in MODEL_CONFIG.keys():
        if key in data and data[key]:
            MODEL_CONFIG[key] = data[key]
            updated.append(key)
    track_mgr.tracks[1]["models"] = MODEL_CONFIG.copy()
    return jsonify({"status": "ok", "updated": updated, "config": MODEL_CONFIG})


@app.route("/get-models", methods=["GET"])
def get_models():
    return jsonify(MODEL_CONFIG)


@app.route("/status", methods=["GET"])
def status():
    stats = get_stats()
    stats["pipeline"] = {
        "running": PIPELINE_STATE["running"],
        "current_phase": PIPELINE_STATE["current_phase"]
    }
    stats["tracks"] = track_mgr.get_status()
    return jsonify(stats)


@app.route("/gpu-status", methods=["GET"])
def gpu_status():
    import requests as req
    loaded = []
    vram_used = 0
    try:
        r = req.get(f"{OLLAMA_URL}/api/ps", timeout=5)
        if r.status_code == 200:
            for m in r.json().get("models", []):
                loaded.append(m.get("name", "?"))
                vram_used += m.get("size_vram", m.get("size", 0))
    except Exception:
        pass
    return jsonify({
        "vram_used_mb": vram_used // (1024*1024),
        "vram_total_mb": int(os.environ.get("VRAM_TOTAL_MB", 7680)),
        "loaded_models": loaded,
        "gpu_locked": track_mgr.gpu_lock.locked()
    })


@app.route("/gpu-free", methods=["POST"])
def gpu_free():
    try:
        ollama.unload_all()
        _add_history("gpu_free")
        return jsonify({"status": "ok", "message": "GPU zwolnione"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ═══════════════════════════════════════════
# PROMPTS MANAGEMENT (pełne z v0.22.0 + nowe)
# ═══════════════════════════════════════════

@app.route("/prompts", methods=["GET"])
def prompts_list_endpoint():
    """Lista wszystkich promptów z metadanymi."""
    return jsonify({"prompts": list_prompts_fn(), "directory": PROMPTS_DIR})


@app.route("/prompt/<name>", methods=["GET"])
def get_prompt_endpoint(name):
    """Pokaż pełną treść promptu."""
    names = get_all_prompt_names()
    if name not in names:
        return jsonify({"error": f"Nieznany prompt: {name}", "available": names}), 404
    return jsonify(get_prompt_with_info(name))


@app.route("/prompt/<name>", methods=["POST"])
def save_prompt_endpoint(name):
    """Zapisz nową treść promptu (kompatybilność z v0.22.0)."""
    names = get_all_prompt_names()
    if name not in names:
        return jsonify({"error": f"Nieznany prompt: {name}", "available": names}), 404
    data = request.json or {}
    content = data.get("content", "")
    if not content.strip():
        return jsonify({"error": "Treść promptu nie może być pusta"}), 400
    result = save_prompt(name, content)
    _add_history("prompt_save", {"name": name, "size": len(content)})
    return jsonify({"status": "ok", **result})


@app.route("/prompt/<name>/save", methods=["POST"])
def save_prompt_endpoint_v2(name):
    """Zapisz nową treść promptu (nowa ścieżka v0.23.0)."""
    return save_prompt_endpoint(name)


@app.route("/prompt/<name>/reset", methods=["POST"])
def reset_prompt_endpoint(name):
    """Resetuj prompt do domyślnej wersji."""
    names = get_all_prompt_names()
    if name not in names:
        return jsonify({"error": f"Nieznany prompt: {name}", "available": names}), 404
    result = reset_prompt(name)
    _add_history("prompt_reset", {"name": name})
    return jsonify({"status": "ok", **result})


@app.route("/prompt/<name>/diff", methods=["GET"])
def diff_prompt(name):
    """Porównaj aktualny prompt z domyślnym."""
    names = get_all_prompt_names()
    if name not in names:
        return jsonify({"error": f"Nieznany prompt: {name}"}), 404

    info = get_prompt_with_info(name)
    current = info.get("content", "")
    # Pobierz default z _DEFAULTS przez get_prompt_with_info
    # Jeśli source == "default", to current == default
    
    # Użyjemy prostszego podejścia
    from pipeline.prompts import _DEFAULTS
    default = _DEFAULTS.get(name, "")

    current_lines = current.strip().split("\n")
    default_lines = default.strip().split("\n")

    added = [l for l in current_lines if l not in default_lines]
    removed = [l for l in default_lines if l not in current_lines]

    return jsonify({
        "name": name,
        "customized": info.get("customized", False),
        "current_lines": len(current_lines),
        "default_lines": len(default_lines),
        "added": added[:20],
        "removed": removed[:20],
        "added_count": len(added),
        "removed_count": len(removed)
    })


@app.route("/prompts/init", methods=["POST"])
def init_prompts_endpoint():
    """Inicjalizuj domyślne pliki promptów."""
    created = init_default_prompts()
    return jsonify({"status": "ok", "created": created})


@app.route("/prompts/reload", methods=["POST"])
def reload_prompts_endpoint():
    """Przeładuj prompty z plików (czyści cache)."""
    # Wyczyść cache w module prompts
    from pipeline.prompts import _cache
    _cache.clear()
    _add_history("reload_prompts")
    return jsonify({"status": "ok", "message": "Cache promptów wyczyszczony"})


@app.route("/prompts/editor", methods=["GET"])
def prompts_editor():
    """Webowy edytor promptów."""
    return Response("""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>WP Pipeline — Edytor Promptów</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font:14px/1.6 'Segoe UI',system-ui,sans-serif;background:#1a1b26;color:#c0caf5;padding:24px;max-width:960px;margin:0 auto}
h1{color:#7aa2f7;margin-bottom:20px;font-size:22px}
select{width:100%;padding:10px 12px;background:#24283b;color:#c0caf5;border:1px solid #3b4261;border-radius:6px;font-size:14px;margin-bottom:10px;cursor:pointer}
textarea{width:100%;height:62vh;padding:14px;background:#1f2335;color:#c0caf5;border:1px solid #3b4261;border-radius:6px;font:13px/1.5 'Cascadia Code','Fira Code','JetBrains Mono',monospace;resize:vertical;tab-size:4}
textarea:focus{border-color:#7aa2f7;outline:none}
.bar{display:flex;gap:8px;margin:8px 0;align-items:center;flex-wrap:wrap}
button{padding:8px 20px;border:none;border-radius:6px;cursor:pointer;font-weight:600;font-size:13px;transition:opacity .2s}
button:hover{opacity:.85}
.save{background:#9ece6a;color:#1a1b26}.reset{background:#f7768e;color:#1a1b26}.diff{background:#7aa2f7;color:#1a1b26}
.info{color:#565f89;font-size:12px;flex:1;text-align:right}
#msg{padding:10px 14px;border-radius:6px;margin:8px 0;font-size:13px;display:none}
.ok{background:#1a3a2a;color:#9ece6a;display:block}.err{background:#3a1a2a;color:#f7768e;display:block}
footer{margin-top:16px;color:#3b4261;font-size:11px;text-align:center}
.tracks{margin-top:16px;padding:12px;background:#24283b;border-radius:6px;font-size:12px}
.tracks h3{color:#7aa2f7;margin-bottom:8px}
</style></head><body>
<h1>&#128221; Edytor Prompt&oacute;w &mdash; WP Pipeline v0.23.0</h1>
<select id="sel" onchange="load()"><option value="">&#8212; wybierz prompt &#8212;</option></select>
<div id="msg"></div>
<div class="bar">
<button class="save" onclick="save()">&#128190; Zapisz</button>
<button class="reset" onclick="rst()">&#8617;&#65039; Reset</button>
<button class="diff" onclick="diff()">&#128200; Diff</button>
<span class="info" id="info">Ctrl+S = zapis</span>
</div>
<textarea id="txt" spellcheck="false" placeholder="Wybierz prompt z listy..."></textarea>
<div class="tracks" id="tracks"></div>
<footer>Pliki: /data/prompts/*.txt &bull; Backupy: /data/prompts/backups/ &bull; v0.23.0 Multi-Track</footer>
<script>
const S=document.getElementById('sel'),T=document.getElementById('txt'),I=document.getElementById('info');
const L={dispatcher:'M0 Dispatcher',classifier:'M2 Classifier',strategist:'M3 Strategist',
writer:'M4 Writer',reviewer:'M5 Reviewer',translator:'M6 Translator',chat:'Chat',modify:'Modify'};
async function init(){try{
const r=await(await fetch('/prompts')).json();const v=S.value;
S.innerHTML='<option value="">-- wybierz prompt --</option>';
r.prompts.forEach(p=>{const o=document.createElement('option');o.value=p.name;
o.textContent=(L[p.name]||p.name)+' ['+(p.source==='file'&&p.customized?'zmieniony':'domyslny')+'] ('+(p.file_size||p.default_size)+' zn.)';
S.appendChild(o)});if(v)S.value=v;
loadTracks()}catch(e){msg('Blad ladowania: '+e.message,'err')}}
async function loadTracks(){try{
const r=await(await fetch('/tracks')).json();
let h='<h3>&#128204; Tracki</h3>';
h+='<div>Multi-track: '+(r.multi_track?'ON':'OFF')+'</div>';
for(const[tid,t]of Object.entries(r.tracks||{})){
const ico=t.enabled?(t.healthy?'&#9989;':'&#10060;'):'&#9898;';
h+=`<div>${ico} Track ${tid} (${t.type}) ${t.active?'ACTIVE: '+t.active.phase:''}</div>`}
document.getElementById('tracks').innerHTML=h}catch(e){}}
async function load(){const n=S.value;if(!n)return;try{
const r=await(await fetch('/prompt/'+n)).json();T.value=r.content;
I.textContent=r.source+(r.modified?' | '+r.modified.slice(0,16):'')+' | '+r.content.length+' zn.';msg('')}catch(e){msg('Blad: '+e.message,'err')}}
async function save(){const n=S.value;if(!n||!T.value.trim()){msg('Wybierz prompt i wpisz tresc','err');return}
try{const r=await(await fetch('/prompt/'+n,{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({content:T.value})})).json();
r.status==='ok'?msg('Zapisano: '+n+' ('+r.size+' zn.)','ok'):msg('Blad: '+(r.error||'?'),'err');init()}catch(e){msg('Blad: '+e.message,'err')}}
async function rst(){const n=S.value;if(!n||!confirm('Resetowac "'+n+'" do domyslnego?\\nPoprzednia wersja -> backup.'))return;
try{await fetch('/prompt/'+n+'/reset',{method:'POST'});msg('Zresetowano: '+n,'ok');load();init()}catch(e){msg('Blad: '+e.message,'err')}}
async function diff(){const n=S.value;if(!n){msg('Wybierz prompt','err');return}
try{const r=await(await fetch('/prompt/'+n+'/diff')).json();
let m=r.customized?'ZMIENIONY':'DOMYSLNY';
m+=` | +${r.added_count} -${r.removed_count} linii`;
if(r.added.length)m+='\\n\\nDodane:\\n'+r.added.slice(0,5).join('\\n');
if(r.removed.length)m+='\\n\\nUsuniete:\\n'+r.removed.slice(0,5).join('\\n');
alert(m)}catch(e){msg('Blad: '+e.message,'err')}}
function msg(t,c){const m=document.getElementById('msg');m.textContent=t;m.className=c||'';m.style.display=c?'block':'none'}
document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key==='s'){e.preventDefault();save()}});
T.addEventListener('input',()=>{const m=I.textContent.match(/(.*\\|)\\s*\\d+/);I.textContent=(m?m[1]+' ':'')+T.value.length+' zn.'});
init();setInterval(loadTracks,10000);
</script></body></html>""", mimetype="text/html")


# ═══════════════════════════════════════════
# M1: SCANNER
# ═══════════════════════════════════════════

@app.route("/inbox", methods=["GET"])
def inbox():
    inbox_dir = os.path.join(DATA_DIR, "inbox")
    os.makedirs(inbox_dir, exist_ok=True)
    files = []
    for filename in os.listdir(inbox_dir):
        filepath = os.path.join(inbox_dir, filename)
        if os.path.isfile(filepath):
            stat = os.stat(filepath)
            files.append({"filename": filename, "size": stat.st_size,
                           "modified": datetime.fromtimestamp(stat.st_mtime).isoformat()})
    return jsonify({"path": inbox_dir, "count": len(files),
                     "files": sorted(files, key=lambda x: x["filename"])})


@app.route("/scan", methods=["POST"])
def scan():
    data = request.json or {}
    scanner = Scanner(data_dir=DATA_DIR)
    _add_history("scan", {"force": data.get("force", False)})
    return ndjson_stream(scanner.scan_all(), "M1")


@app.route("/upload", methods=["POST"])
def upload():
    data = request.json or {}
    files = data.get("files", [])
    if not files:
        return jsonify({"error": "Brak plików"}), 400
    scanner = Scanner(data_dir=DATA_DIR)
    results = scanner.save_uploaded_files(files)
    _add_history("upload", {"count": len(files)})
    return jsonify({"saved": results})


# ═══════════════════════════════════════════
# DOCUMENTS
# ═══════════════════════════════════════════

@app.route("/document/<int:doc_id>", methods=["GET"])
def get_document(doc_id):
    db = get_db()
    doc = db.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc:
        return jsonify({"error": f"Dokument #{doc_id} nie istnieje"}), 404
    result = dict(doc)
    if not request.args.get("full"):
        if result.get("content"):
            result["content"] = result["content"][:500] + "..." if len(result["content"]) > 500 else result["content"]
    classification = db.execute("SELECT * FROM classifications WHERE document_id = ?", (doc_id,)).fetchone()
    if classification:
        result["classification"] = dict(classification)
    return jsonify(result)


@app.route("/document/<int:doc_id>/delete", methods=["POST"])
def delete_document(doc_id):
    db = get_db()
    doc = db.execute("SELECT filename FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc:
        return jsonify({"error": f"Dokument #{doc_id} nie istnieje"}), 404
    db.execute("DELETE FROM classifications WHERE document_id = ?", (doc_id,))
    db.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    db.commit()
    _add_history("document_delete", {"id": doc_id})
    return jsonify({"status": "ok"})


@app.route("/document/<int:doc_id>/archive", methods=["POST"])
def archive_document(doc_id):
    db = get_db()
    db.execute("UPDATE documents SET status = 'archived' WHERE id = ?", (doc_id,))
    db.commit()
    return jsonify({"status": "ok"})


@app.route("/duplicates", methods=["GET"])
def duplicates():
    db = get_db()
    rows = db.execute("SELECT checksum, GROUP_CONCAT(filename) as filenames, COUNT(*) as count FROM documents GROUP BY checksum HAVING count > 1").fetchall()
    return jsonify({"duplicates": [dict(r) for r in rows]})


@app.route("/failed", methods=["GET"])
def failed_files():
    failed_dir = os.path.join(DATA_DIR, "failed")
    result = {"categories": {}}
    if os.path.exists(failed_dir):
        for category in os.listdir(failed_dir):
            cat_path = os.path.join(failed_dir, category)
            if os.path.isdir(cat_path):
                files = os.listdir(cat_path)
                result["categories"][category] = {"count": len(files), "files": files[:20]}
    return jsonify(result)


@app.route("/failed/retry", methods=["POST"])
def retry_failed():
    failed_dir = os.path.join(DATA_DIR, "failed")
    inbox_dir = os.path.join(DATA_DIR, "inbox")
    moved = 0
    if os.path.exists(failed_dir):
        for category in os.listdir(failed_dir):
            cat_path = os.path.join(failed_dir, category)
            if os.path.isdir(cat_path):
                for filename in os.listdir(cat_path):
                    src = os.path.join(cat_path, filename)
                    dst = os.path.join(inbox_dir, filename)
                    if os.path.isfile(src):
                        shutil.move(src, dst)
                        moved += 1
    return jsonify({"status": "ok", "moved": moved})


# ═══════════════════════════════════════════
# M2: CLASSIFIER
# ═══════════════════════════════════════════

@app.route("/classify", methods=["POST"])
def classify():
    data = request.json or {}
    client, model, tid = track_mgr.get_client("classifier")
    classifier = Classifier(client, model=model)
    _add_history("classify", {"track": tid, "model": model, "document_id": data.get("document_id"), "force": data.get("force", False)})

    def gen():
        yield {"event": "track_selected", "track": tid,
               "type": track_mgr.tracks[tid]["type"], "model": model}
        if track_mgr.tracks[tid]["type"] == "local":
            with track_mgr.gpu_lock:
                track_mgr.set_active(tid, "M2", model)
                try:
                    for ev in classifier.classify_all(
                        document_id=data.get("document_id"),
                        project=data.get("project"),
                        force=data.get("force", False)):
                        yield ev
                finally:
                    track_mgr.clear_active(tid)
        else:
            track_mgr.set_active(tid, "M2", model)
            try:
                for ev in classifier.classify_all(
                    document_id=data.get("document_id"),
                    project=data.get("project"),
                    force=data.get("force", False)):
                    yield ev
            finally:
                track_mgr.clear_active(tid)

    return ndjson_stream(gen(), "M2")


@app.route("/classification/<int:doc_id>", methods=["GET"])
def get_classification(doc_id):
    db = get_db()
    c = db.execute("SELECT * FROM classifications WHERE document_id = ?", (doc_id,)).fetchone()
    if not c:
        return jsonify({"error": f"Brak klasyfikacji dla #{doc_id}"}), 404
    result = dict(c)
    for field in ["tags", "key_facts"]:
        if result.get(field):
            try:
                result[field] = json.loads(result[field])
            except:
                pass
    return jsonify(result)


@app.route("/materials", methods=["GET"])
def materials():
    project = request.args.get("project", "")
    min_usefulness = request.args.get("min_usefulness", type=int)
    db = get_db()
    query = "SELECT d.id, d.filename, d.status, d.char_count, d.created_at, c.project, c.category, c.usefulness, c.summary FROM documents d LEFT JOIN classifications c ON d.id = c.document_id WHERE 1=1"
    params = []
    if project:
        query += " AND c.project LIKE ?"
        params.append(f"%{project}%")
    if min_usefulness:
        query += " AND c.usefulness >= ?"
        params.append(min_usefulness)
    query += " ORDER BY c.project, c.usefulness DESC"
    rows = db.execute(query, params).fetchall()
    return jsonify({"materials": [dict(r) for r in rows]})


@app.route("/projects", methods=["GET"])
def projects():
    db = get_db()
    rows = db.execute("SELECT c.project as name, COUNT(DISTINCT d.id) as doc_count, ROUND(AVG(c.usefulness),1) as avg_usefulness FROM classifications c JOIN documents d ON c.document_id=d.id WHERE d.status IN ('classified','used') GROUP BY c.project ORDER BY doc_count DESC").fetchall()
    return jsonify({"projects": [dict(r) for r in rows]})


@app.route("/tags", methods=["GET"])
def tags():
    db = get_db()
    rows = db.execute("SELECT tags FROM classifications WHERE tags IS NOT NULL").fetchall()
    tag_count = {}
    for row in rows:
        try:
            for tag in json.loads(row["tags"]):
                tag_count[tag] = tag_count.get(tag, 0) + 1
        except:
            pass
    sorted_tags = sorted(tag_count.items(), key=lambda x: x[1], reverse=True)
    return jsonify({"tags": [{"name": t[0], "count": t[1]} for t in sorted_tags]})


@app.route("/search", methods=["GET"])
def search():
    query = request.args.get("q", "")
    if not query:
        return jsonify({"results": []})
    db = get_db()
    like = f"%{query}%"
    rows = db.execute("SELECT d.id, d.filename, d.status, c.project, c.category, c.usefulness, c.summary FROM documents d LEFT JOIN classifications c ON c.document_id=d.id WHERE d.content LIKE ? OR d.filename LIKE ? OR c.project LIKE ? OR c.summary LIKE ? ORDER BY c.usefulness DESC LIMIT 30", (like, like, like, like)).fetchall()
    return jsonify({"results": [dict(r) for r in rows]})


# ═══════════════════════════════════════════
# M3: STRATEGIST
# ═══════════════════════════════════════════

@app.route("/plan", methods=["POST"])
def plan():
    data = request.json or {}
    client, model, tid = track_mgr.get_client("strategist")
    strategist = Strategist(client, model=model)
    _add_history("plan", {"project": data.get("project"), "track": tid})

    def gen():
        yield {"event": "track_selected", "track": tid,
               "type": track_mgr.tracks[tid]["type"], "model": model}
        if track_mgr.tracks[tid]["type"] == "local":
            with track_mgr.gpu_lock:
                track_mgr.set_active(tid, "M3", model)
                try:
                    for ev in strategist.plan(
                        project=data.get("project"),
                        target_words=data.get("target_words"),
                        check_only=data.get("check_only", False),
                        series=data.get("series", False)):
                        yield ev
                finally:
                    track_mgr.clear_active(tid)
        else:
            track_mgr.set_active(tid, "M3", model)
            try:
                for ev in strategist.plan(
                    project=data.get("project"),
                    target_words=data.get("target_words"),
                    check_only=data.get("check_only", False),
                    series=data.get("series", False)):
                    yield ev
            finally:
                track_mgr.clear_active(tid)

    return ndjson_stream(gen(), "M3")


@app.route("/briefs", methods=["GET"])
def briefs_list():
    db = get_db()
    rows = db.execute("SELECT id, title, topic, status, target_words, wp_category, source_ids, created_at FROM briefs ORDER BY created_at DESC").fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["source_count"] = len(json.loads(item.get("source_ids", "[]")))
        except:
            item["source_count"] = 0
        result.append(item)
    return jsonify({"briefs": result})


@app.route("/brief/<int:brief_id>", methods=["GET"])
def get_brief(brief_id):
    db = get_db()
    brief = db.execute("SELECT * FROM briefs WHERE id = ?", (brief_id,)).fetchone()
    if not brief:
        return jsonify({"error": f"Brief #{brief_id} nie istnieje"}), 404
    result = dict(brief)
    for field in ["structure", "source_ids", "wp_tags"]:
        if result.get(field):
            try:
                result[field] = json.loads(result[field])
            except:
                pass
    return jsonify(result)


@app.route("/brief/<int:brief_id>/delete", methods=["POST"])
def delete_brief(brief_id):
    db = get_db()
    db.execute("DELETE FROM briefs WHERE id = ?", (brief_id,))
    db.commit()
    return jsonify({"status": "ok"})


# ═══════════════════════════════════════════
# M4+M5+M6: WRITE (z live streaming)
# ═══════════════════════════════════════════

@app.route("/write", methods=["POST"])
def write():
    data = request.json or {}
    brief_id = data.get("brief_id")
    article_id = data.get("article_id")
    skip_review = data.get("skip_review", False)
    skip_translate = data.get("skip_translate", False)
    only_review = data.get("only_review", False)
    only_translate = data.get("only_translate", False)
    rewrite = data.get("rewrite", False)
    target_words = data.get("target_words")
    live_stream = data.get("live_stream", True)

    def generate():
        nonlocal article_id
        try:
            # ── Only Review ──
            if only_review and article_id:
                client, model, tid = track_mgr.get_client("reviewer")
                yield {"event": "track_selected", "track": tid, "model": model, "phase": "M5"}
                rv = Reviewer(client, model=model)
                if track_mgr.tracks[tid]["type"] == "local":
                    with track_mgr.gpu_lock:
                        track_mgr.set_active(tid, "M5", model)
                        try:
                            for ev in rv.review(article_id=article_id,
                                                code_only=data.get("code_only", False),
                                                quality_threshold=data.get("quality_threshold", 7)):
                                yield ev
                        finally:
                            track_mgr.clear_active(tid)
                else:
                    track_mgr.set_active(tid, "M5", model)
                    try:
                        for ev in rv.review(article_id=article_id,
                                            code_only=data.get("code_only", False),
                                            quality_threshold=data.get("quality_threshold", 7)):
                            yield ev
                    finally:
                        track_mgr.clear_active(tid)
                return

            # ── Only Translate ──
            if only_translate and article_id:
                client, model, tid = track_mgr.get_client("translator")
                yield {"event": "track_selected", "track": tid, "model": model, "phase": "M6"}
                tr = Translator(client, model=model)
                if track_mgr.tracks[tid]["type"] == "local":
                    with track_mgr.gpu_lock:
                        track_mgr.set_active(tid, "M6", model)
                        try:
                            for ev in tr.translate(article_id=article_id,
                                                   style=data.get("style"),
                                                   force=data.get("force", False),
                                                   live_stream=live_stream):
                                yield ev
                        finally:
                            track_mgr.clear_active(tid)
                else:
                    track_mgr.set_active(tid, "M6", model)
                    try:
                        for ev in tr.translate(article_id=article_id,
                                               style=data.get("style"),
                                               force=data.get("force", False),
                                               live_stream=live_stream):
                            yield ev
                    finally:
                        track_mgr.clear_active(tid)
                return

            # ── M4: Writer ──
            client_w, model_w, tid_w = track_mgr.get_client("writer")
            yield {"event": "track_selected", "track": tid_w, "model": model_w, "phase": "M4"}

            def run_writer():
                nonlocal article_id
                w = Writer(client_w, model=model_w)
                for ev in w.write(brief_id=brief_id, article_id=article_id,
                                  target_words=target_words, rewrite=rewrite,
                                  live_stream=live_stream):
                    yield ev
                    if ev.get("event") == "written":
                        db = get_db()
                        art = db.execute("SELECT id FROM articles ORDER BY created_at DESC LIMIT 1").fetchone()
                        if art:
                            article_id = art["id"]

            if track_mgr.tracks[tid_w]["type"] == "local":
                with track_mgr.gpu_lock:
                    track_mgr.set_active(tid_w, "M4", model_w)
                    try:
                        for ev in run_writer():
                            yield ev
                    finally:
                        track_mgr.clear_active(tid_w)
            else:
                track_mgr.set_active(tid_w, "M4", model_w)
                try:
                    for ev in run_writer():
                        yield ev
                finally:
                    track_mgr.clear_active(tid_w)

            if not article_id:
                yield {"event": "error", "message": "Writer nie utworzył artykułu"}
                return

            # ── M5: Reviewer ──
            if not skip_review:
                client_r, model_r, tid_r = track_mgr.get_client("reviewer")
                yield {"event": "track_selected", "track": tid_r, "model": model_r, "phase": "M5"}
                rv = Reviewer(client_r, model=model_r)
                if track_mgr.tracks[tid_r]["type"] == "local":
                    with track_mgr.gpu_lock:
                        track_mgr.set_active(tid_r, "M5", model_r)
                        try:
                            for ev in rv.review(article_id=article_id):
                                yield ev
                        finally:
                            track_mgr.clear_active(tid_r)
                else:
                    track_mgr.set_active(tid_r, "M5", model_r)
                    try:
                        for ev in rv.review(article_id=article_id):
                            yield ev
                    finally:
                        track_mgr.clear_active(tid_r)
            else:
                yield {"event": "skipped", "phase": "M5", "message": "Korekta pominięta"}
                db = get_db()
                db.execute("UPDATE articles SET status='reviewed', content_en_rev=content_en WHERE id=?", (article_id,))
                db.commit()

            # ── M6: Translator ──
            if not skip_translate:
                client_t, model_t, tid_t = track_mgr.get_client("translator")
                yield {"event": "track_selected", "track": tid_t, "model": model_t, "phase": "M6"}
                tr = Translator(client_t, model=model_t)
                if track_mgr.tracks[tid_t]["type"] == "local":
                    with track_mgr.gpu_lock:
                        track_mgr.set_active(tid_t, "M6", model_t)
                        try:
                            for ev in tr.translate(article_id=article_id,
                                                   style=data.get("style"),
                                                   live_stream=live_stream):
                                yield ev
                        finally:
                            track_mgr.clear_active(tid_t)
                else:
                    track_mgr.set_active(tid_t, "M6", model_t)
                    try:
                        for ev in tr.translate(article_id=article_id,
                                               style=data.get("style"),
                                               live_stream=live_stream):
                            yield ev
                    finally:
                        track_mgr.clear_active(tid_t)
            else:
                yield {"event": "skipped", "phase": "M6", "message": "Tłumaczenie pominięte"}

        except Exception as e:
            yield {"event": "error", "message": str(e)}
        finally:
            try:
                ollama.unload_all()
            except Exception:
                pass

    _add_history("write", {"brief_id": brief_id, "article_id": article_id})
    return Response((json.dumps(e, ensure_ascii=False) + "\n" for e in generate()),
                    mimetype="application/x-ndjson")


# ═══════════════════════════════════════════
# ARTICLES & REVIEW
# ═══════════════════════════════════════════

@app.route("/articles", methods=["GET"])
def articles_list():
    db = get_db()
    rows = db.execute("SELECT id, brief_id, title_en, title_pl, status, wp_post_id, wp_post_url, review_score, LENGTH(content_en)/6 as word_count_en, LENGTH(content_pl)/6 as word_count_pl, created_at FROM articles ORDER BY created_at DESC").fetchall()
    return jsonify({"articles": [dict(r) for r in rows]})


@app.route("/preview", methods=["GET"])
def preview():
    db = get_db()
    article_id = request.args.get("id")
    if article_id:
        article = db.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
    else:
        article = db.execute("SELECT * FROM articles ORDER BY created_at DESC LIMIT 1").fetchone()
    if not article:
        return jsonify({"error": "Brak artykułu"}), 404
    return jsonify(dict(article))


@app.route("/article/<int:article_id>/approve", methods=["POST"])
def approve_article(article_id):
    db = get_db()
    db.execute("UPDATE articles SET status='reviewed' WHERE id=?", (article_id,))
    db.commit()
    return jsonify({"status": "ok"})


@app.route("/review/<int:article_id>", methods=["GET"])
def get_review(article_id):
    db = get_db()
    article = db.execute("SELECT id, title_en, review_score, review_notes, reviewer_model, status FROM articles WHERE id=?", (article_id,)).fetchone()
    if not article:
        return jsonify({"error": f"Artykuł #{article_id} nie istnieje"}), 404
    result = dict(article)
    if result.get("review_notes"):
        try:
            result["issues"] = json.loads(result["review_notes"])
        except:
            result["issues"] = []
    return jsonify(result)


@app.route("/review/<int:article_id>/issues", methods=["GET"])
def get_review_issues(article_id):
    db = get_db()
    article = db.execute("SELECT review_notes FROM articles WHERE id=?", (article_id,)).fetchone()
    if not article:
        return jsonify({"error": f"Artykuł #{article_id} nie istnieje"}), 404
    issues = []
    if article["review_notes"]:
        try:
            issues = json.loads(article["review_notes"])
        except:
            pass
    return jsonify({
        "article_id": article_id, "total": len(issues),
        "high": len([i for i in issues if i.get("severity") == "HIGH"]),
        "low": len([i for i in issues if i.get("severity") != "HIGH"]),
        "issues": issues
    })


# ═══════════════════════════════════════════
# TRANSLATION EXCEPTIONS
# ═══════════════════════════════════════════

@app.route("/exceptions", methods=["GET"])
def get_exceptions():
    return jsonify({"exceptions": sorted(list(TRANSLATION_EXCEPTIONS)),
                    "count": len(TRANSLATION_EXCEPTIONS)})


@app.route("/exceptions/add", methods=["POST"])
def add_exception():
    data = request.json or {}
    word = data.get("word", "").strip()
    if not word:
        return jsonify({"error": "Brak słowa"}), 400
    TRANSLATION_EXCEPTIONS.add(word)
    return jsonify({"status": "ok", "word": word})


@app.route("/exceptions/remove", methods=["POST"])
def remove_exception():
    data = request.json or {}
    word = data.get("word", "").strip()
    TRANSLATION_EXCEPTIONS.discard(word)
    return jsonify({"status": "ok", "word": word})


# ═══════════════════════════════════════════
# M7: PUBLISHER & WORDPRESS
# ═══════════════════════════════════════════

@app.route("/publish", methods=["POST"])
def publish():
    data = request.json or {}
    if data.get("wp_url"):
        os.environ["WP_URL"] = data["wp_url"]
    if data.get("wp_user"):
        os.environ["WP_USER"] = data["wp_user"]
    if data.get("wp_app_password"):
        os.environ["WP_APP_PASSWORD"] = data["wp_app_password"]
    pub = Publisher()
    result = pub.publish(article_id=data.get("article_id"))
    if result.get("event") == "published":
        _add_history("publish", {"wp_post_id": result.get("wp_post_id")})
    return jsonify(result)


@app.route("/publish/update", methods=["POST"])
def publish_update():
    data = request.json or {}
    article_id = data.get("article_id")
    if not article_id:
        return jsonify({"error": "Brak article_id"}), 400
    db = get_db()
    article = db.execute("SELECT wp_post_id FROM articles WHERE id=?", (article_id,)).fetchone()
    if not article or not article["wp_post_id"]:
        return jsonify({"error": "Artykuł nie opublikowany"}), 400
    return jsonify({"status": "ok", "wp_post_id": article["wp_post_id"]})


@app.route("/wp-test", methods=["GET"])
def wp_test():
    from pipeline.wp_session import WordPressSession
    wp_url = os.environ.get("WP_URL", "")
    wp_user = os.environ.get("WP_USER", "")
    wp_pass = os.environ.get("WP_APP_PASSWORD", "")
    if not wp_url:
        return jsonify({"status": "error", "error": "WP_URL not set"})
    try:
        wp = WordPressSession(wp_url)
        wp.set_credentials(wp_user, wp_pass)
        result = wp.test_connection()
        return jsonify({"status": "ok" if result.get("auth_ok") else "error",
                        "wp_url": wp_url, "wp_user": wp_user, **result})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)[:200]})


@app.route("/wp-categories", methods=["GET"])
def wp_categories():
    from pipeline.wp_session import WordPressSession
    wp_url = os.environ.get("WP_URL", "")
    if not wp_url:
        return jsonify({"error": "WP_URL not set"}), 400
    try:
        wp = WordPressSession(wp_url)
        wp.set_credentials(os.environ.get("WP_USER", ""), os.environ.get("WP_APP_PASSWORD", ""))
        r = wp.get("/?rest_route=/wp/v2/categories&per_page=100")
        if r.status_code == 200:
            return jsonify({"categories": [{"id": c["id"], "name": c["name"], "count": c["count"]} for c in r.json()]})
        return jsonify({"error": f"HTTP {r.status_code}"}), r.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/wp-tags", methods=["GET"])
def wp_tags():
    from pipeline.wp_session import WordPressSession
    wp_url = os.environ.get("WP_URL", "")
    if not wp_url:
        return jsonify({"error": "WP_URL not set"}), 400
    try:
        wp = WordPressSession(wp_url)
        wp.set_credentials(os.environ.get("WP_USER", ""), os.environ.get("WP_APP_PASSWORD", ""))
        r = wp.get("/?rest_route=/wp/v2/tags&per_page=100")
        if r.status_code == 200:
            return jsonify({"tags": [{"id": t["id"], "name": t["name"], "count": t["count"]} for t in r.json()]})
        return jsonify({"error": f"HTTP {r.status_code}"}), r.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/wp-post/<int:post_id>", methods=["GET"])
def wp_post(post_id):
    from pipeline.wp_session import WordPressSession
    try:
        wp = WordPressSession(os.environ.get("WP_URL", ""))
        wp.set_credentials(os.environ.get("WP_USER", ""), os.environ.get("WP_APP_PASSWORD", ""))
        r = wp.get(f"/?rest_route=/wp/v2/posts/{post_id}")
        if r.status_code == 200:
            return jsonify(r.json())
        return jsonify({"error": f"HTTP {r.status_code}"}), r.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/wp-sync", methods=["POST"])
def wp_sync():
    from pipeline.wp_session import WordPressSession
    db = get_db()
    articles = db.execute("SELECT id, wp_post_id FROM articles WHERE wp_post_id IS NOT NULL").fetchall()
    if not articles:
        return jsonify({"status": "ok", "synced": 0})
    try:
        wp = WordPressSession(os.environ.get("WP_URL", ""))
        wp.set_credentials(os.environ.get("WP_USER", ""), os.environ.get("WP_APP_PASSWORD", ""))
        synced = 0
        for article in articles:
            r = wp.get(f"/?rest_route=/wp/v2/posts/{article['wp_post_id']}")
            if r.status_code == 200:
                db.execute("UPDATE articles SET wp_post_url=? WHERE id=?",
                           (r.json().get("link", ""), article["id"]))
                synced += 1
        db.commit()
        return jsonify({"status": "ok", "synced": synced})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════
# FULL PIPELINE (M1→M7)
# ═══════════════════════════════════════════

@app.route("/full-pipeline", methods=["POST"])
def full_pipeline():
    data = request.json or {}
    project = data.get("project")
    skip_review = data.get("skip_review", False)
    skip_translate = data.get("skip_translate", False)
    skip_publish = data.get("skip_publish", False)
    live_stream = data.get("live_stream", True)

    def generate():
        pipeline_start = time.time()
        PIPELINE_STATE["running"] = True
        try:
            yield {"event": "pipeline_start", "message": "Pipeline M1→M7"}

            # M1: Scanner (bez GPU)
            if PIPELINE_STATE["stop_requested"]:
                yield {"event": "stopped", "message": "Pipeline zatrzymany"}
                return
            scanner = Scanner(data_dir=DATA_DIR)
            for ev in scanner.scan_all():
                yield ev

            # M2: Classifier
            if PIPELINE_STATE["stop_requested"]:
                yield {"event": "stopped", "message": "Pipeline zatrzymany"}
                return
            client_c, model_c, tid_c = track_mgr.get_client("classifier")
            yield {"event": "track_selected", "track": tid_c, "model": model_c, "phase": "M2"}
            classifier = Classifier(client_c, model=model_c)
            if track_mgr.tracks[tid_c]["type"] == "local":
                with track_mgr.gpu_lock:
                    track_mgr.set_active(tid_c, "M2", model_c)
                    try:
                        for ev in classifier.classify_all():
                            yield ev
                    finally:
                        track_mgr.clear_active(tid_c)
            else:
                track_mgr.set_active(tid_c, "M2", model_c)
                try:
                    for ev in classifier.classify_all():
                        yield ev
                finally:
                    track_mgr.clear_active(tid_c)

            # M3: Strategist
            if PIPELINE_STATE["stop_requested"]:
                yield {"event": "stopped", "message": "Pipeline zatrzymany"}
                return
            client_s, model_s, tid_s = track_mgr.get_client("strategist")
            yield {"event": "track_selected", "track": tid_s, "model": model_s, "phase": "M3"}
            strategist = Strategist(client_s, model=model_s)
            brief_created = False
            if track_mgr.tracks[tid_s]["type"] == "local":
                with track_mgr.gpu_lock:
                    track_mgr.set_active(tid_s, "M3", model_s)
                    try:
                        for ev in strategist.plan(project=project):
                            yield ev
                            if ev.get("event") == "brief_created":
                                brief_created = True
                    finally:
                        track_mgr.clear_active(tid_s)
            else:
                track_mgr.set_active(tid_s, "M3", model_s)
                try:
                    for ev in strategist.plan(project=project):
                        yield ev
                        if ev.get("event") == "brief_created":
                            brief_created = True
                finally:
                    track_mgr.clear_active(tid_s)

            if not brief_created:
                yield {"event": "pipeline_stopped", "message": "Za mało materiałów"}
                return

            # M4: Writer
            if PIPELINE_STATE["stop_requested"]:
                yield {"event": "stopped", "message": "Pipeline zatrzymany"}
                return
            client_w, model_w, tid_w = track_mgr.get_client("writer")
            yield {"event": "track_selected", "track": tid_w, "model": model_w, "phase": "M4"}
            writer = Writer(client_w, model=model_w)
            article_id = None
            if track_mgr.tracks[tid_w]["type"] == "local":
                with track_mgr.gpu_lock:
                    track_mgr.set_active(tid_w, "M4", model_w)
                    try:
                        for ev in writer.write(live_stream=live_stream):
                            yield ev
                            if ev.get("event") == "written":
                                db = get_db()
                                art = db.execute("SELECT id FROM articles ORDER BY created_at DESC LIMIT 1").fetchone()
                                if art:
                                    article_id = art["id"]
                    finally:
                        track_mgr.clear_active(tid_w)
            else:
                track_mgr.set_active(tid_w, "M4", model_w)
                try:
                    for ev in writer.write(live_stream=live_stream):
                        yield ev
                        if ev.get("event") == "written":
                            db = get_db()
                            art = db.execute("SELECT id FROM articles ORDER BY created_at DESC LIMIT 1").fetchone()
                            if art:
                                article_id = art["id"]
                finally:
                    track_mgr.clear_active(tid_w)

            if not article_id:
                yield {"event": "error", "message": "Writer nie utworzył artykułu"}
                return

            # M5: Reviewer
            if not skip_review:
                if PIPELINE_STATE["stop_requested"]:
                    yield {"event": "stopped", "message": "Pipeline zatrzymany"}
                    return
                client_r, model_r, tid_r = track_mgr.get_client("reviewer")
                yield {"event": "track_selected", "track": tid_r, "model": model_r, "phase": "M5"}
                rv = Reviewer(client_r, model=model_r)
                if track_mgr.tracks[tid_r]["type"] == "local":
                    with track_mgr.gpu_lock:
                        track_mgr.set_active(tid_r, "M5", model_r)
                        try:
                            for ev in rv.review(article_id=article_id):
                                yield ev
                        finally:
                            track_mgr.clear_active(tid_r)
                else:
                    track_mgr.set_active(tid_r, "M5", model_r)
                    try:
                        for ev in rv.review(article_id=article_id):
                            yield ev
                    finally:
                        track_mgr.clear_active(tid_r)
            else:
                db = get_db()
                db.execute("UPDATE articles SET status='reviewed', content_en_rev=content_en WHERE id=?",
                           (article_id,))
                db.commit()

            # M6: Translator
            if not skip_translate:
                if PIPELINE_STATE["stop_requested"]:
                    yield {"event": "stopped", "message": "Pipeline zatrzymany"}
                    return
                client_t, model_t, tid_t = track_mgr.get_client("translator")
                yield {"event": "track_selected", "track": tid_t, "model": model_t, "phase": "M6"}
                tr = Translator(client_t, model=model_t)
                if track_mgr.tracks[tid_t]["type"] == "local":
                    with track_mgr.gpu_lock:
                        track_mgr.set_active(tid_t, "M6", model_t)
                        try:
                            for ev in tr.translate(article_id=article_id, live_stream=live_stream):
                                yield ev
                        finally:
                            track_mgr.clear_active(tid_t)
                else:
                    track_mgr.set_active(tid_t, "M6", model_t)
                    try:
                        for ev in tr.translate(article_id=article_id, live_stream=live_stream):
                            yield ev
                    finally:
                        track_mgr.clear_active(tid_t)

            # M7: Publisher
            if not skip_publish:
                pub = Publisher()
                yield pub.publish(article_id=article_id)

            elapsed = int(time.time() - pipeline_start)
            yield {"event": "pipeline_done", "elapsed_seconds": elapsed,
                   "message": f"Pipeline zakończony w {elapsed}s"}

        except Exception as e:
            yield {"event": "error", "message": str(e)}
        finally:
            PIPELINE_STATE["running"] = False
            PIPELINE_STATE["stop_requested"] = False
            try:
                ollama.unload_all()
            except Exception:
                pass
            yield {"event": "gpu_freed", "message": "VRAM zwolniony"}

    _add_history("full_pipeline", {"project": project})
    return Response((json.dumps(e, ensure_ascii=False) + "\n" for e in generate()),
                    mimetype="application/x-ndjson")


# ═══════════════════════════════════════════
# CHAT & DISPATCH (preferują API)
# ═══════════════════════════════════════════

@app.route("/dispatch", methods=["POST"])
def dispatch():
    data = request.json or {}
    client, model, tid = track_mgr.get_client("dispatcher", prefer_api=True)
    d = Dispatcher(client, model=model)
    result = d.dispatch(message=data.get("message", ""),
                        has_files=data.get("has_files", False),
                        history=data.get("history"))
    result["track"] = tid
    return jsonify(result)


@app.route("/chat", methods=["POST"])
def chat():
    data = request.json or {}
    client, model, tid = track_mgr.get_client("chat", prefer_api=True)
    d = Dispatcher(client, model=model)
    return jsonify({
        "response": d.chat(message=data.get("message", ""),
                           context=data.get("context", "")),
        "track": tid
    })


@app.route("/modify", methods=["POST"])
def modify():
    data = request.json or {}
    client, model, tid = track_mgr.get_client("dispatcher", prefer_api=True)
    d = Dispatcher(client, model=model)
    result = d.modify(message=data.get("message", ""))
    _add_history("modify", {"track": tid, "message": data.get("message", "")[:100]})
    return jsonify(result)


# ═══════════════════════════════════════════
# PIPELINE CONTROL & UTILITIES
# ═══════════════════════════════════════════

@app.route("/pipeline/stop", methods=["POST"])
def pipeline_stop():
    if not PIPELINE_STATE["running"]:
        return jsonify({"status": "ok", "message": "Pipeline nie uruchomiony"})
    PIPELINE_STATE["stop_requested"] = True
    return jsonify({"status": "ok", "message": "Żądanie zatrzymania wysłane"})


@app.route("/history", methods=["GET"])
def history():
    limit = request.args.get("n", 50, type=int)
    return jsonify({"history": PIPELINE_STATE["history"][:limit],
                    "total": len(PIPELINE_STATE["history"])})


@app.route("/queue", methods=["GET"])
def queue():
    return jsonify({"queue": [], "running": PIPELINE_STATE["running"],
                    "current_phase": PIPELINE_STATE["current_phase"]})


@app.route("/logs", methods=["GET"])
def logs():
    n = request.args.get("n", 30, type=int)
    log_file = os.path.join(DATA_DIR, "logs", "pipeline.log")
    lines = []
    if os.path.exists(log_file):
        try:
            with open(log_file, "r") as f:
                lines = f.readlines()[-n:]
        except Exception:
            pass
    return jsonify({"lines": [l.strip() for l in lines], "count": len(lines)})


@app.route("/backup", methods=["POST"])
def backup():
    backup_dir = os.path.join(DATA_DIR, "backups")
    os.makedirs(backup_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    src = os.path.join(DATA_DIR, "db", "pipeline.db")
    dst = os.path.join(backup_dir, f"pipeline_{ts}.db")
    if os.path.exists(src):
        shutil.copy2(src, dst)
    _add_history("backup", {"file": dst})
    return jsonify({"status": "ok", "backup_file": dst, "timestamp": ts})


# ═══════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════

if __name__ == "__main__":
    init_db()
    init_default_prompts()
    port = int(os.environ.get("FLASK_PORT", os.environ.get("WORKER_PORT", 5002)))
    log.info(f"=== WP Pipeline Worker v0.23.0 Multi-Track on port {port} ===")
    log.info(f"Ollama: {ollama.base_url}")
    log.info(f"Data dir: {DATA_DIR}")
    log.info(f"Prompts dir: {PROMPTS_DIR}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)