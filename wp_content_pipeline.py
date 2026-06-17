"""
title: WP Content Pipeline
author: pipeline-bot
version: 0.23.0
"""

from pydantic import BaseModel, Field
from typing import Union, Generator, Iterator
import json
import re as _re


class Pipeline:
    class Valves(BaseModel):
        # ── General ──
        WORKER_URL: str = Field(default="http://10.2.10.20:5002", description="Worker API URL")
        MULTI_TRACK_MODE: str = Field(
            default="local-only",
            description="Track mode: local-only / fallback / parallel"
        )
        ENABLE_AI_CHAT: bool = Field(default=False, description="AI chat dla nieznanych komend")
        ENABLE_LIVE_STREAM: bool = Field(default=True, description="Live streaming przy pisaniu/tłumaczeniu")

        # ── Track 1: Local (Ollama) ──
        TRACK1_ENABLED: bool = Field(default=True, description="Track 1 — Local GPU (Ollama)")
        MODEL_DISPATCHER: str = Field(default="qwen2.5:3b", description="T1 M0/M2 Dispatcher+Classifier (~2GB)")
        MODEL_STRATEGIST: str = Field(default="qwen2.5:7b-instruct", description="T1 M3 Strategist (~5GB)")
        MODEL_WRITER: str = Field(default="llama3.1:8b", description="T1 M4 Writer (~5GB)")
        MODEL_REVIEWER: str = Field(default="mistral:7b-instruct", description="T1 M5 Reviewer (~4.5GB)")
        MODEL_TRANSLATOR: str = Field(default="qwen2.5:7b-instruct", description="T1 M6 Translator (~5GB)")
        MODEL_CHAT: str = Field(default="qwen2.5:3b", description="T1 Chat model")

        # ── Track 2: API ──
        TRACK2_ENABLED: bool = Field(default=False, description="Track 2 — API (OpenAI/LiteLLM)")
        TRACK2_URL: str = Field(default="https://api.openai.com/v1", description="T2 API Base URL")
        TRACK2_API_KEY: str = Field(default="", description="T2 API Key")
        T2_DISPATCHER: str = Field(default="gpt-4o-mini", description="T2 Dispatcher")
        T2_STRATEGIST: str = Field(default="gpt-4o", description="T2 Strategist")
        T2_WRITER: str = Field(default="gpt-4o", description="T2 Writer")
        T2_REVIEWER: str = Field(default="gpt-4o-mini", description="T2 Reviewer")
        T2_TRANSLATOR: str = Field(default="gpt-4o", description="T2 Translator")
        T2_CHAT: str = Field(default="gpt-4o-mini", description="T2 Chat")

        # ── Track 3: API Fallback ──
        TRACK3_ENABLED: bool = Field(default=False, description="Track 3 — API Fallback")
        TRACK3_URL: str = Field(default="", description="T3 API Base URL")
        TRACK3_API_KEY: str = Field(default="", description="T3 API Key")
        T3_DISPATCHER: str = Field(default="", description="T3 Dispatcher")
        T3_STRATEGIST: str = Field(default="", description="T3 Strategist")
        T3_WRITER: str = Field(default="", description="T3 Writer")
        T3_REVIEWER: str = Field(default="", description="T3 Reviewer")
        T3_TRANSLATOR: str = Field(default="", description="T3 Translator")
        T3_CHAT: str = Field(default="", description="T3 Chat")

        # ── Track 4: API Fallback 2 ──
        TRACK4_ENABLED: bool = Field(default=False, description="Track 4 — API Fallback 2")
        TRACK4_URL: str = Field(default="", description="T4 API Base URL")
        TRACK4_API_KEY: str = Field(default="", description="T4 API Key")
        T4_DISPATCHER: str = Field(default="", description="T4 Dispatcher")
        T4_STRATEGIST: str = Field(default="", description="T4 Strategist")
        T4_WRITER: str = Field(default="", description="T4 Writer")
        T4_REVIEWER: str = Field(default="", description="T4 Reviewer")
        T4_TRANSLATOR: str = Field(default="", description="T4 Translator")
        T4_CHAT: str = Field(default="", description="T4 Chat")

        # ── WordPress ──
        WP_URL: str = Field(default="https://phetnar.my-board.org", description="WordPress URL")
        WP_USER: str = Field(default="pipeline-bot", description="WordPress user")
        WP_APP_PASSWORD: str = Field(default="", description="WordPress App Password")

    def __init__(self):
        self.name = "WP Content Pipeline"
        self.valves = self.Valves()
        self._last_sync_hash = None
        self._sync_tracks()

    # ═══════════════════════════════════════════
    # TRACK CONFIG & SYNC
    # ═══════════════════════════════════════════

    def _build_track_config(self) -> dict:
        """Buduj konfigurację tracków z Valves → JSON dla /set-tracks."""
        v = self.valves
        config = {
            "multi_track": v.MULTI_TRACK_MODE != "local-only",
            "track1": {
                "enabled": v.TRACK1_ENABLED,
                "models": {
                    "dispatcher": v.MODEL_DISPATCHER,
                    "classifier": v.MODEL_DISPATCHER,
                    "strategist": v.MODEL_STRATEGIST,
                    "writer": v.MODEL_WRITER,
                    "reviewer": v.MODEL_REVIEWER,
                    "translator": v.MODEL_TRANSLATOR,
                    "chat": v.MODEL_CHAT,
                }
            }
        }
        for tid, prefix in [(2, "T2"), (3, "T3"), (4, "T4")]:
            enabled = getattr(v, f"TRACK{tid}_ENABLED", False)
            url = getattr(v, f"TRACK{tid}_URL", "")
            api_key = getattr(v, f"TRACK{tid}_API_KEY", "")
            models = {}
            for module in ["dispatcher", "strategist", "writer", "reviewer", "translator", "chat"]:
                val = getattr(v, f"{prefix}_{module.upper()}", "")
                if val:
                    models[module] = val
            if models.get("dispatcher"):
                models["classifier"] = models["dispatcher"]
            config[f"track{tid}"] = {
                "enabled": enabled and bool(url),
                "url": url,
                "api_key": api_key,
                "models": models
            }
        return config

    def _sync_tracks(self):
        """Wyślij konfigurację tracków do Workera."""
        config = self._build_track_config()
        self._last_sync_hash = hash(json.dumps(config, sort_keys=True))
        try:
            import requests as _rq
            try:
                r = _rq.post(f"{self.valves.WORKER_URL}/set-tracks", json=config, timeout=5)
                if r.status_code == 200:
                    return
            except Exception:
                pass
            _rq.post(f"{self.valves.WORKER_URL}/set-models", json=self._models(), timeout=3)
        except Exception:
            pass

    def _check_sync(self):
        """Re-sync jeśli Valves się zmieniły."""
        config = self._build_track_config()
        current_hash = hash(json.dumps(config, sort_keys=True))
        if current_hash != self._last_sync_hash:
            self._sync_tracks()

    # ═══════════════════════════════════════════
    # MAIN ROUTER
    # ═══════════════════════════════════════════

    def pipe(self, body: dict, **kwargs) -> Union[str, Generator, Iterator]:
        import requests
        self._check_sync()

        msgs = body.get("messages", [])
        if not msgs:
            return "Brak wiadomości."
        raw = msgs[-1].get("content", "").strip()
        cmd = raw.lower().strip()
        w = self.valves.WORKER_URL
        N = chr(10)

        # ── POMOC ──
        if cmd in ("pomoc", "help", "?"):
            return self._help()

        # ── M0 INFO ──
        if cmd == "test":
            return self._test(w)
        if cmd == "status":
            return self._cmd_status(w)
        if cmd in ("gpu", "status gpu", "vram"):
            return self._cmd_gpu(w)
        if cmd in ("wersja", "version"):
            return self._cmd_version(w)
        if cmd in ("health", "zdrowie"):
            return self._cmd_health(w)

        # ── TRACKS & MONITORING ──
        if cmd in ("track status", "tracki", "tracks"):
            return self._cmd_track_status(w)
        if _re.match(r'^track\s+test\s+(\d)$', cmd):
            tid = _re.search(r'(\d)', cmd).group(1)
            return self._cmd_track_test(w, tid)
        if cmd in ("track test", "track testy"):
            return self._cmd_track_test(w)
        if cmd in ("track sync", "sync tracks", "track resync"):
            self._sync_tracks()
            return chr(9989) + " Track config zsynchronizowany z Workerem."
        if cmd in ("procesy", "processes"):
            return self._cmd_processes(w)

        # ── PROMPTY ──
        if cmd in ("prompty", "prompt list", "prompts"):
            return self._cmd_prompts_list(w)
        if cmd == "prompt reload":
            return self._cmd_prompt_reload(w)
        if _re.match(r'^prompt\s+reset\s+(\w+)$', cmd):
            name = _re.search(r'^prompt\s+reset\s+(\w+)$', cmd).group(1)
            return self._cmd_prompt_reset(w, name)
        if _re.match(r'^prompt\s+diff\s+(\w+)$', cmd):
            name = _re.search(r'^prompt\s+diff\s+(\w+)$', cmd).group(1)
            return self._cmd_prompt_diff(w, name)
        if _re.match(r'^prompt\s+(\w+)$', cmd):
            name = _re.search(r'^prompt\s+(\w+)$', cmd).group(1)
            if name not in ("list", "reload"):
                return self._cmd_prompt_show(w, name)

        # ── M1 SCANNER ──
        if cmd == "skanuj":
            return self._stream(w, "/scan", "M1: Skanowanie inbox")
        if cmd.startswith("skanuj "):
            force = "--force" in cmd
            filename = raw[7:].strip().replace("--force", "").strip()
            return self._stream(w, "/scan", "M1: Skanowanie", {"filename": filename, "force": force})
        if cmd == "inbox":
            return self._cmd_inbox(w)
        if cmd in ("dokumenty", "documents", "docs"):
            return self._cmd_dokumenty(w)
        if _re.match(r'^dokument\s+tekst\s+(\d+)$', cmd):
            doc_id = _re.search(r'(\d+)', cmd).group(1)
            return self._cmd_dokument(w, doc_id, full=True)
        if _re.match(r'^dokument\s+(\d+)$', cmd):
            doc_id = _re.search(r'(\d+)', cmd).group(1)
            return self._cmd_dokument(w, doc_id, full=False)
        if cmd == "duplikaty":
            return self._cmd_duplikaty(w)
        if _re.match(r'^usun\s+dokument\s+(\d+)$', cmd):
            doc_id = _re.search(r'(\d+)', cmd).group(1)
            return self._cmd_usun_dokument(w, doc_id)
        if cmd == "failed":
            return self._cmd_failed(w)
        if cmd == "failed retry":
            return self._cmd_failed_retry(w)

        # ── M2 CLASSIFIER ──
        if cmd == "klasyfikuj":
            return self._stream(w, "/classify", "M2: Klasyfikacja dokumentów")
        if _re.match(r'^klasyfikuj\s+(\d+)$', cmd):
            doc_id = int(_re.search(r'(\d+)', cmd).group(1))
            return self._stream(w, "/classify", f"M2: Klasyfikacja dok #{doc_id}", {"document_id": doc_id})
        if _re.match(r'^reklasyfikuj\s+(\d+)', cmd):
            doc_id = int(_re.search(r'(\d+)', cmd).group(1))
            hint = raw.split("--", 1)[1].strip() if "--" in raw else ""
            return self._stream(w, "/classify", f"M2: Reklasyfikacja #{doc_id}", {"document_id": doc_id, "force": True, "hint": hint})
        if cmd.startswith("klasyfikuj projekt "):
            proj = raw[19:].strip()
            return self._stream(w, "/classify", f"M2: Klasyfikacja projektu {proj}", {"project": proj})
        if _re.match(r'^klasyfikacja\s+(\d+)$', cmd):
            doc_id = _re.search(r'(\d+)', cmd).group(1)
            return self._cmd_klasyfikacja(w, doc_id)
        if cmd == "projekty":
            return self._cmd_projects(w)
        if cmd.startswith("materialy") or cmd.startswith("materiały"):
            return self._cmd_materials(w, raw)
        if cmd.startswith("szukaj "):
            return self._cmd_search(w, raw[7:].strip())
        if cmd == "tagi":
            return self._cmd_tagi(w)
        if _re.match(r'^przydatnosc\s+(\d+)$', cmd):
            min_u = _re.search(r'(\d+)', cmd).group(1)
            return self._cmd_przydatnosc(w, min_u)

        # ── M3 STRATEGIST ──
        if cmd == "zaplanuj":
            return self._stream(w, "/plan", "M3: Planowanie artykułu")
        if cmd.startswith("zaplanuj "):
            return self._cmd_zaplanuj(w, cmd, raw)
        if cmd in ("briefy", "briefs"):
            return self._cmd_briefs(w)
        if _re.match(r'^brief\s+(\d+)$', cmd):
            brief_id = int(_re.search(r'(\d+)', cmd).group(1))
            return self._cmd_brief_detail(w, brief_id)
        if _re.match(r'^usun\s+brief\s+(\d+)$', cmd):
            brief_id = _re.search(r'(\d+)', cmd).group(1)
            return self._cmd_usun_brief(w, brief_id)
        if cmd in ("materialy gotowe", "materiały gotowe", "materialy gotowe?", "materiały gotowe?"):
            return self._stream(w, "/plan", "M3: Sprawdzam materiały", {"check_only": True})

        # ── M4 WRITER ──
        if _re.match(r'^napisz\b', cmd):
            return self._cmd_napisz(w, cmd, raw)
        if _re.match(r'^przepisz\s+(\d+)$', cmd):
            aid = int(_re.search(r'(\d+)', cmd).group(1))
            return self._stream(w, "/write", f"M4: Przepisanie #{aid}",
                                {"article_id": aid, "rewrite": True, "live_stream": self.valves.ENABLE_LIVE_STREAM})
        if cmd in ("artykuly", "articles", "artykuły"):
            return self._cmd_articles(w)
        if _re.match(r'^artykul\s+en\s+(\d+)$', cmd):
            aid = int(_re.search(r'(\d+)', cmd).group(1))
            return self._cmd_article_en(w, aid)

        # ── M5 REVIEWER ──
        if _re.match(r'^koryguj\b', cmd):
            return self._cmd_koryguj(w, cmd)
        if _re.match(r'^korekta\s+(\d+)$', cmd):
            aid = _re.search(r'(\d+)', cmd).group(1)
            return self._cmd_korekta(w, aid)
        if _re.match(r'^issues\s+(\d+)$', cmd):
            aid = _re.search(r'(\d+)', cmd).group(1)
            return self._cmd_issues(w, aid)
        if _re.match(r'^zatwierdz\s+(\d+)$', cmd):
            aid = _re.search(r'(\d+)', cmd).group(1)
            return self._cmd_zatwierdz(w, aid)

        # ── M6 TRANSLATOR ──
        if _re.match(r'^przetlumacz\b', cmd) or _re.match(r'^przetłumacz\b', cmd):
            return self._cmd_przetlumacz(w, cmd, raw)
        if _re.match(r'^tlumaczenie\s+(\d+)$', cmd) or _re.match(r'^tłumaczenie\s+(\d+)$', cmd):
            aid = _re.search(r'(\d+)', cmd).group(1)
            return self._cmd_preview_id(w, aid)
        if cmd in ("podglad", "podgląd", "preview"):
            return self._cmd_preview(w)
        if _re.match(r'^podglad\s+(\d+)$', cmd) or _re.match(r'^podgląd\s+(\d+)$', cmd):
            aid = _re.search(r'(\d+)', cmd).group(1)
            return self._cmd_preview_id(w, aid)
        if _re.match(r'^porownaj\s+(\d+)$', cmd) or _re.match(r'^porównaj\s+(\d+)$', cmd):
            aid = int(_re.search(r'(\d+)', cmd).group(1))
            return self._cmd_porownaj(w, aid)
        if cmd == "wyjatki" or cmd == "wyjątki":
            return self._cmd_wyjatki(w)
        if cmd.startswith("wyjatki dodaj ") or cmd.startswith("wyjątki dodaj "):
            word = raw.split("dodaj ", 1)[1].strip() if "dodaj " in raw else ""
            return self._cmd_wyjatki_dodaj(w, word)
        if cmd.startswith("wyjatki usun ") or cmd.startswith("wyjątki usuń "):
            word = raw.split("usun ", 1)[1].strip() if "usun " in raw else raw.split("usuń ", 1)[1].strip()
            return self._cmd_wyjatki_usun(w, word)

        # ── M7 PUBLISHER ──
        if cmd == "opublikuj":
            return self._cmd_publish(w)
        if _re.match(r'^opublikuj\s+(\d+)$', cmd):
            aid = int(_re.search(r'(\d+)', cmd).group(1))
            return self._cmd_publish(w, aid)
        if _re.match(r'^aktualizuj\s+(\d+)$', cmd):
            aid = int(_re.search(r'(\d+)', cmd).group(1))
            return self._cmd_aktualizuj(w, aid)
        if cmd in ("wp test", "wordpress test"):
            return self._cmd_wp_test(w)
        if cmd in ("wp config", "wordpress config"):
            return self._cmd_wp_config()
        if cmd in ("wp posty", "wp posts"):
            return self._cmd_wp_posts(w)
        if cmd in ("wp kategorie", "wp categories"):
            return self._cmd_wp_kategorie(w)
        if cmd in ("wp tagi", "wp tags"):
            return self._cmd_wp_tagi(w)
        if _re.match(r'^wp\s+post\s+(\d+)$', cmd):
            post_id = _re.search(r'(\d+)', cmd).group(1)
            return self._cmd_wp_post(w, post_id)
        if cmd == "synchronizuj":
            return self._cmd_synchronizuj(w)

        # ── GLOBAL PIPELINE ──
        if _re.match(r'^(pelny\s+pipeline|pełny\s+pipeline|full\s+pipeline|full|pipeline|zrob\s+wszystko|zrób\s+wszystko|caly\s+proces|cały\s+proces)', cmd):
            return self._cmd_full_pipeline(w, cmd, raw)
        if cmd in ("zatrzymaj pipeline", "stop pipeline", "stop"):
            return self._cmd_stop_pipeline(w)

        # ── STATUS & MONITORING ──
        if cmd == "status kolejka":
            return self._cmd_kolejka(w)
        if cmd == "historia":
            return self._cmd_historia(w)
        if cmd in ("zwolnij gpu", "gpu free"):
            return self._cmd_gpu_free(w)
        if cmd.startswith("logi"):
            n = "30"
            m = _re.search(r'(\d+)', cmd)
            if m:
                n = m.group(1)
            return self._cmd_logi(w, n)

        # ── EKSPORT & BACKUP ──
        if _re.match(r'^eksport\s+en\s+(\d+)$', cmd):
            aid = _re.search(r'(\d+)', cmd).group(1)
            return self._cmd_export(w, aid, lang="en")
        if _re.match(r'^eksport\s+(\d+)$', cmd):
            aid = _re.search(r'(\d+)', cmd).group(1)
            return self._cmd_export(w, aid)
        if cmd == "eksport":
            return self._cmd_export(w, None)
        if cmd.startswith("backup"):
            return self._cmd_backup(w)

        # ── UWAGI ──
        if _re.match(r'^(uwaga|popraw|zmien|zmień|dodaj|usun|usuń|zostaw)\b', cmd):
            return self._cmd_uwaga(w, raw)
        if _re.match(r'^popraw\s+(\d+)$', cmd):
            aid = _re.search(r'(\d+)', cmd).group(1)
            return self._cmd_koryguj(w, f"koryguj {aid}")

        # ── AI CHAT (M0) ──
        if cmd in ("czat", "chat"):
            if not self.valves.ENABLE_AI_CHAT:
                return self._ai_off_msg()
            return "Tryb czatu aktywny. Napisz pytanie."
        if cmd in ("co mamy", "co mamy?"):
            return self._cmd_status(w)

        # ── FALLBACK ──
        return self._chat(w, raw)

    # ═══════════════════════════════════════════
    # HELP
    # ═══════════════════════════════════════════

    def _help(self):
        N = chr(10)
        S = chr(9552)
        D = chr(9472)
        B = chr(8226)
        A = chr(8594)
        v = self.valves
        mode = v.MULTI_TRACK_MODE
        active_tracks = ["T1"] if v.TRACK1_ENABLED else []
        if v.TRACK2_ENABLED: active_tracks.append("T2")
        if v.TRACK3_ENABLED: active_tracks.append("T3")
        if v.TRACK4_ENABLED: active_tracks.append("T4")
        track_info = f"Tracki: {', '.join(active_tracks)} | Tryb: {mode}"

        return N.join([
            S*40,
            chr(128221) + " **WP Content Pipeline v0.23.0**",
            f"  {track_info}",
            S*40, "",

            chr(128200) + " **Status i info**",
            f"  {B} `status` {D} statystyki pipeline",
            f"  {B} `gpu` {D} VRAM i załadowane modele",
            f"  {B} `test` {D} test połączenia z Worker",
            f"  {B} `health` {D} health check systemu",
            f"  {B} `wersja` {D} wersje komponentów i tracki",
            f"  {B} `historia` {D} ostatnie operacje",
            "",

            chr(128204) + " **Tracki i monitoring**",
            f"  {B} `track status` {D} status wszystkich tracków",
            f"  {B} `track test` {D} test connectivity tracków",
            f"  {B} `track test <N>` {D} test konkretnego tracku",
            f"  {B} `track sync` {D} re-sync config z Workerem",
            f"  {B} `procesy` {D} aktywne procesy na trackach",
            "",

            chr(128220) + " **Prompty**",
            f"  {B} `prompty` {D} lista promptów",
            f"  {B} `prompt <name>` {D} pokaż treść promptu",
            f"  {B} `prompt reset <name>` {D} resetuj do domyślnego",
            f"  {B} `prompt diff <name>` {D} porównaj ze domyślnym",
            f"  {B} `prompt reload` {D} przeładuj z plików",
            "",

            chr(128193) + " **M1: Skanowanie** " + D + " 0 GB",
            f"  {B} `skanuj` {D} skanuj /data/inbox/",
            f"  {B} `skanuj --force` {D} ignoruj duplikaty",
            f"  {B} `inbox` {D} pliki w inbox",
            f"  {B} `dokumenty` {D} lista dokumentów w bazie",
            f"  {B} `dokument <id>` {D} szczegóły dokumentu",
            f"  {B} `dokument tekst <id>` {D} pełna treść",
            f"  {B} `failed` {D} pliki z błędami",
            f"  {B} `failed retry` {D} ponów przetwarzanie",
            "",

            chr(127991) + chr(65039) + " **M2: Klasyfikacja** " + D + " ~2 GB",
            f"  {B} `klasyfikuj` {D} klasyfikuj nowe dokumenty",
            f"  {B} `klasyfikuj <id>` {D} klasyfikuj dokument #ID",
            f"  {B} `reklasyfikuj <id>` {D} ponowna klasyfikacja",
            f"  {B} `projekty` {D} lista projektów",
            f"  {B} `materialy` {D} materiały pogrupowane",
            f"  {B} `materialy <projekt>` {D} materiały z projektu",
            f"  {B} `szukaj <fraza>` {D} wyszukaj w bazie",
            f"  {B} `tagi` {D} lista tagów",
            "",

            chr(128203) + " **M3: Planowanie** " + D + " ~5 GB",
            f"  {B} `zaplanuj` {D} brief z najlepszego projektu",
            f"  {B} `zaplanuj <projekt>` {D} brief dla projektu",
            f"  {B} `zaplanuj --krotki` {D} ~600 słów",
            f"  {B} `zaplanuj --dlugi` {D} ~2000 słów",
            f"  {B} `briefy` {D} lista briefów",
            f"  {B} `brief <id>` {D} szczegóły briefu",
            f"  {B} `materialy gotowe` {D} sprawdź czy są materiały",
            "",

            chr(9997) + chr(65039) + " **M4: Pisanie** " + D + " ~5 GB" + (" " + chr(128997) + " live" if v.ENABLE_LIVE_STREAM else ""),
            f"  {B} `napisz` {D} artykuł EN + korekta + tłumaczenie PL",
            f"  {B} `napisz <brief_id>` {D} z konkretnego briefu",
            f"  {B} `napisz --bez-korekty` {D} pomiń M5",
            f"  {B} `napisz --bez-tlumaczenia` {D} tylko EN",
            f"  {B} `przepisz <id>` {D} przepisz artykuł od nowa",
            f"  {B} `artykuly` {D} lista artykułów",
            "",

            chr(128270) + " **M5: Korekta** " + D + " ~4.5 GB",
            f"  {B} `koryguj` {D} korekta ostatniego artykułu",
            f"  {B} `koryguj <id>` {D} korekta artykułu #ID",
            f"  {B} `korekta <id>` {D} pokaż wynik korekty",
            f"  {B} `issues <id>` {D} lista problemów",
            f"  {B} `zatwierdz <id>` {D} zatwierdź mimo issues",
            "",

            chr(127477) + chr(127473) + " **M6: Tłumaczenie** " + D + " ~5 GB" + (" " + chr(128997) + " live" if v.ENABLE_LIVE_STREAM else ""),
            f"  {B} `przetlumacz` {D} tłumacz ostatni artykuł EN{A}PL",
            f"  {B} `przetlumacz <id>` {D} tłumacz artykuł #ID",
            f"  {B} `przetlumacz --formalnie` {D} styl formalny",
            f"  {B} `podglad` {D} pokaż ostatni artykuł PL",
            f"  {B} `podglad <id>` {D} pokaż artykuł #ID",
            f"  {B} `porownaj <id>` {D} EN i PL obok siebie",
            f"  {B} `wyjatki` {D} słowa NIE tłumaczone",
            "",

            chr(127760) + " **M7: WordPress** " + D + " 0 GB",
            f"  {B} `opublikuj` {D} draft na WordPress",
            f"  {B} `opublikuj <id>` {D} publikuj artykuł #ID",
            f"  {B} `aktualizuj <id>` {D} zaktualizuj post WP",
            f"  {B} `wp test` {D} test połączenia",
            f"  {B} `wp config` {D} konfiguracja WP",
            f"  {B} `wp posty` {D} lista postów",
            "",

            chr(128640) + " **Pipeline**",
            f"  {B} `pelny pipeline` {D} M1{A}M7 (~20 min local)",
            f"  {B} `pelny pipeline <projekt>` {D} dla projektu",
            f"  {B} `pelny pipeline --bez-korekty` {D} pomiń M5",
            f"  {B} `pelny pipeline --fast` {D} preferuj API (szybciej)",
            f"  {B} `zatrzymaj pipeline` {D} graceful stop",
            f"  {B} `zwolnij gpu` {D} wyładuj modele",
            "",

            chr(9999) + chr(65039) + " **Uwagi i poprawki**",
            f"  {B} `uwaga: <tekst>` {D} feedback po etapie",
            f"  {B} `popraw: <tekst>` {D} popraw artykuł",
            "",

            chr(128230) + " **Eksport i backup**",
            f"  {B} `eksport` {D} eksportuj artykuł jako MD",
            f"  {B} `eksport <id>` {D} eksportuj #ID",
            f"  {B} `backup` {D} backup bazy",
            f"  {B} `logi` {D} logi workera",
            "",

            D*40,
            chr(128161) + " **Szybki start:**",
            f"  1. Skopiuj pliki do `/data/inbox/`",
            f"  2. `skanuj` {A} `klasyfikuj` {A} `zaplanuj`",
            f"  3. `napisz` {A} `podglad` {A} `opublikuj`",
            "  Lub: `pelny pipeline`",
        ])

    # ═══════════════════════════════════════════
    # HELPERS
    # ═══════════════════════════════════════════

    def _get(self, w, path, timeout=15):
        import requests
        try:
            r = requests.get(w + path, timeout=timeout)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        except requests.exceptions.Timeout:
            return {"_error": "Timeout - worker nie odpowiada"}
        except Exception as e:
            return {"_error": str(e)}

    def _post(self, w, path, data=None, timeout=60):
        import requests
        try:
            r = requests.post(w + path, json=data or {}, timeout=timeout)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        except requests.exceptions.Timeout:
            return {"_error": "Timeout - worker nie odpowiada"}
        except Exception as e:
            return {"_error": str(e)}

    def _models(self):
        """Track 1 model config."""
        return {
            "dispatcher": self.valves.MODEL_DISPATCHER,
            "classifier": self.valves.MODEL_DISPATCHER,
            "strategist": self.valves.MODEL_STRATEGIST,
            "writer": self.valves.MODEL_WRITER,
            "reviewer": self.valves.MODEL_REVIEWER,
            "translator": self.valves.MODEL_TRANSLATOR,
            "chat": self.valves.MODEL_CHAT,
        }

    def _wp_data(self):
        d = self._models()
        if self.valves.WP_URL:
            d["wp_url"] = self.valves.WP_URL
        if self.valves.WP_USER:
            d["wp_user"] = self.valves.WP_USER
        if self.valves.WP_APP_PASSWORD:
            d["wp_app_password"] = self.valves.WP_APP_PASSWORD
        return d

    def _err(self, data):
        if data is None:
            return True
        if isinstance(data, dict) and "_error" in data:
            return True
        return False

    def _err_msg(self, data, label=""):
        if data is None:
            return chr(9203) + f" `{label}` — endpoint nie istnieje w Worker."
        if isinstance(data, dict) and "_error" in data:
            return chr(10060) + f" {label}: {data['_error']}"
        return ""

    def _ico(self, status):
        m = {
            "new": chr(127381), "classified": chr(127991) + chr(65039),
            "used": chr(128206), "created": chr(128203),
            "writing": chr(9997) + chr(65039), "done": chr(9989),
            "ready": chr(128203), "draft_en": chr(127468) + chr(127463),
            "reviewed": chr(128270), "translated": chr(127477) + chr(127473),
            "published": chr(127760), "archived": chr(128451)
        }
        return m.get(status, chr(8226))

    def _track_ico(self, track_type):
        """Return icon for track type."""
        icons = {
            "ollama": chr(127942),  # 🖶 
            "openai": chr(128187),  # 🌐
            "litellm": chr(128187), # 🌐
            "api": chr(128187),     # 🌐
            "fallback": chr(128295) # 🚧
        }
        return icons.get(track_type, chr(11035))  # # jako domyślny

    def _ai_off_msg(self):
        N = chr(10)
        return N.join([
            chr(10067) + " AI Chat jest **wyłączony**.",
            "",
            "Włącz w: **Settings " + chr(8594) + " Valves " + chr(8594) + " ENABLE_AI_CHAT**",
            "",
            "Wpisz `pomoc` aby zobaczyć dostępne komendy.",
        ])

    # ═══════════════════════════════════════════
    # TRACK MANAGEMENT
    # ═══════════════════════════════════════════

    def _cmd_track_status(self, w):
        d = self._get(w, "/tracks")
        if self._err(d):
            return self._err_msg(d, "track status")
        N = chr(10)
        v = self.valves
        lines = [
            chr(128204) + " **Tracki:**",
            "",
            f"  Multi-track: {'**ON**' if d.get('multi_track') else 'OFF'} (tryb: {v.MULTI_TRACK_MODE})",
            ""
        ]
        for tid_str, info in sorted(d.get("tracks", {}).items(), key=lambda x: str(x[0])):
            tid = str(tid_str)
            enabled = info.get("enabled", False)
            ttype = info.get("type", "?")
            healthy = info.get("healthy", False)
            ico = chr(128994) if (enabled and healthy) else (chr(128993) if enabled else chr(128308))
            status_txt = "OK" if healthy else ("OFF" if not enabled else "FAIL")
            lines.append(f"  {ico} **Track {tid}** [{ttype}] {chr(8212)} {status_txt}")
            if enabled:
                active = info.get("active")
                if active:
                    lines.append(f"    {chr(9203)} Aktywny: **{active.get('phase', '?')}** {chr(8594)} `{active.get('model', '?')}`")
                if ttype == "local":
                    locked = info.get("gpu_locked", False)
                    lines.append(f"    GPU: {'**zajęty**' if locked else 'wolny'}")
                models = info.get("models", {})
                if models:
                    model_items = [f"{k}=`{v}`" for k, v in list(models.items())[:4] if v]
                    if model_items:
                        lines.append(f"    {', '.join(model_items)}")
            lines.append("")
        return N.join(lines)

    def _cmd_track_test(self, w, track_id=None):
        d = self._get(w, "/tracks")
        if self._err(d):
            return self._err_msg(d, "track test")
        N = chr(10)
        lines = [chr(128268) + " **Track Test:**", ""]
        for tid_str, info in sorted(d.get("tracks", {}).items(), key=lambda x: str(x[0])):
            if track_id and str(tid_str) != str(track_id):
                continue
            tid = str(tid_str)
            enabled = info.get("enabled", False)
            healthy = info.get("healthy", False)
            ttype = info.get("type", "?")
            ico = chr(9989) if healthy else (chr(9898) if not enabled else chr(10060))
            label = "OK" if healthy else ("wyłączony" if not enabled else "NIEDOSTĘPNY")
            lines.append(f"  {ico} Track {tid} [{ttype}]: {label}")
        return N.join(lines)

    def _cmd_processes(self, w):
        tracks = self._get(w, "/tracks")
        status = self._get(w, "/status")
        if self._err(tracks):
            return self._err_msg(tracks, "procesy")
        N = chr(10)
        pip = status.get("pipeline", {}) if status and not self._err(status) else {}
        lines = [chr(128200) + " **Aktywne procesy:**", ""]
        if pip.get("running"):
            lines.append(f"  {chr(128994)} Pipeline: **{pip.get('current_phase', '?')}**")
        else:
            lines.append(f"  {chr(9898)} Pipeline: idle")
        lines.append("")
        any_active = False
        for tid_str, info in sorted(tracks.get("tracks", {}).items(), key=lambda x: str(x[0])):
            active = info.get("active")
            if active:
                any_active = True
                lines.append(f"  {chr(9203)} Track {tid_str}: **{active.get('phase', '?')}** {chr(8594)} `{active.get('model', '?')}`")
                if active.get("started"):
                    lines.append(f"    Start: {active['started'][:19]}")
        if not any_active:
            lines.append("  Brak aktywnych zadań na trackach.")
        return N.join(lines)

    # ═══════════════════════════════════════════
    # PROMPT MANAGEMENT
    # ═══════════════════════════════════════════

    def _cmd_prompts_list(self, w):
        d = self._get(w, "/prompts")
        if self._err(d):
            return self._err_msg(d, "prompty")
        N = chr(10)
        prompts = d.get("prompts", [])
        lines = [chr(128220) + f" **Prompty** ({len(prompts)})", ""]
        for p in prompts:
            ico = chr(9998) + chr(65039) if p.get("customized") else chr(128196)
            src = "plik" if p.get("source") == "file" else "domyślny"
            size = p.get("file_size", p.get("default_size", 0))
            lines.append(f"  {ico} `{p.get('name', '?')}` ({src}, {size} zn.)")
        lines += ["", f"  {chr(128161)} `prompt <name>` {chr(8594)} pokaż treść"]
        return N.join(lines)

    def _cmd_prompt_show(self, w, name):
        d = self._get(w, f"/prompt/{name}")
        if self._err(d):
            return self._err_msg(d, f"prompt {name}")
        if d.get("error"):
            return chr(10060) + f" {d['error']}"
        N = chr(10)
        content = d.get("content", "")
        truncated = content[:2000] + "..." if len(content) > 2000 else content
        customized = "tak" if d.get("customized") else "nie"
        return N.join([
            chr(128220) + f" **Prompt: {name}**",
            f"  Źródło: {d.get('source', '?')}",
            f"  Rozmiar: {d.get('length', 0)} zn.",
            f"  Zmieniony: {customized}",
            "", "```", truncated, "```"
        ])

    def _cmd_prompt_reset(self, w, name):
        d = self._post(w, f"/prompt/{name}/reset")
        if self._err(d):
            return self._err_msg(d, f"prompt reset {name}")
        if d and d.get("error"):
            return chr(10060) + f" {d['error']}"
        return chr(9989) + f" Prompt `{name}` zresetowany do domyślnego."

    def _cmd_prompt_diff(self, w, name):
        d = self._get(w, f"/prompt/{name}/diff")
        if self._err(d):
            return self._err_msg(d, f"prompt diff {name}")
        if d and d.get("error"):
            return chr(10060) + f" {d['error']}"
        N = chr(10)
        lines = [
            chr(128269) + f" **Diff: {name}**",
            f"  Zmieniony: {'tak' if d.get('customized') else 'nie'}",
            f"  Linie: {d.get('current_lines', 0)} (domyślnie: {d.get('default_lines', 0)})",
        ]
        added = d.get("added", [])
        removed = d.get("removed", [])
        if added:
            lines += ["", f"  **Dodane ({d.get('added_count', len(added))}):**"]
            for a in added[:10]:
                lines.append(f"  `+ {a[:80]}`")
        if removed:
            lines += ["", f"  **Usunięte ({d.get('removed_count', len(removed))}):**"]
            for r in removed[:10]:
                lines.append(f"  `- {r[:80]}`")
        if not added and not removed:
            lines.append("  Brak różnic.")
        return N.join(lines)

    def _cmd_prompt_reload(self, w):
        d = self._post(w, "/prompts/reload")
        if self._err(d):
            return self._err_msg(d, "prompt reload")
        return chr(9989) + " Prompty przeładowane z plików."

    # ═══════════════════════════════════════════
    # M0: INFO COMMANDS
    # ═══════════════════════════════════════════

    def _test(self, w):
        d = self._get(w, "/health")
        if self._err(d):
            return chr(10060) + f" Worker niedostępny: {w}" + chr(10) + self._err_msg(d, "test")
        N = chr(10)
        ico = chr(9989) if d.get("status") == "ok" else chr(10060)
        return N.join([
            f"{ico} **Worker**",
            f"  Status: {d.get('status', '?')}",
            f"  Ollama: {'OK' if d.get('ollama') else 'FAIL'}",
            f"  Database: {'OK' if d.get('database') else 'FAIL'}",
            f"  Version: {d.get('version', '?')}",
            f"  Multi-track: {d.get('multi_track', False)}",
            f"  Pipeline: {'RUNNING' if d.get('pipeline_running') else 'idle'}",
            f"  URL: `{w}`"
        ])

    def _cmd_status(self, w):
        d = self._get(w, "/status")
        if self._err(d):
            return self._err_msg(d, "status")
        N = chr(10)
        doc = d.get("documents", {})
        brf = d.get("briefs", {})
        art = d.get("articles", {})
        pip = d.get("pipeline", {})
        trk = d.get("tracks", {})

        lines = [chr(128200) + " **Status Pipeline**", ""]

        if pip.get("running"):
            lines.append(chr(128994) + f" **Pipeline aktywny:** {pip.get('current_phase', '?')}")
            lines.append("")

        # Track summary
        if trk.get("multi_track"):
            active_tracks = sum(1 for t in trk.get("tracks", {}).values()
                                if isinstance(t, dict) and t.get("enabled") and t.get("healthy"))
            total_tracks = sum(1 for t in trk.get("tracks", {}).values()
                               if isinstance(t, dict) and t.get("enabled"))
            lines.append(chr(128204) + f" **Tracki:** {active_tracks}/{total_tracks} aktywne (multi-track ON)")
            lines.append("")

        lines.append(chr(128193) + f" **Dokumenty:** {doc.get('total', 0)}")
        for k, v in doc.get("by_status", {}).items():
            lines.append(f"  {self._ico(k)} {k}: {v}")

        lines += ["", chr(128203) + f" **Briefy:** {brf.get('total', 0)}"]
        for k, v in brf.get("by_status", {}).items():
            lines.append(f"  {self._ico(k)} {k}: {v}")

        lines += ["", chr(128221) + f" **Artykuły:** {art.get('total', 0)}"]
        for k, v in art.get("by_status", {}).items():
            lines.append(f"  {self._ico(k)} {k}: {v}")

        return N.join(lines)

    def _cmd_gpu(self, w):
        d = self._get(w, "/gpu-status")
        if self._err(d):
            return self._err_msg(d, "gpu")
        N = chr(10)
        u = d.get("vram_used_mb", 0)
        t = d.get("vram_total_mb", 7680)
        p = int(u / t * 100) if t > 0 else 0
        bf = p // 10
        bar = chr(9619) * bf + chr(9617) * (10 - bf)
        ld = d.get("loaded_models", [])
        locked = d.get("gpu_locked", False)
        return N.join([
            chr(128421) + chr(65039) + " **GPU Status**",
            "",
            f"  VRAM: [{bar}] {u}/{t} MB ({p}%)",
            f"  Załadowane: {', '.join('`' + m + '`' for m in ld) if ld else 'brak'}",
            f"  GPU Lock: {'**zajęty**' if locked else 'wolny'}"
        ])

    def _cmd_version(self, w):
        d = self._get(w, "/health")
        if self._err(d):
            return self._err_msg(d, "wersja")
        N = chr(10)
        v = self.valves
        lines = [
            chr(128230) + " **Wersje**",
            "",
            "  Pipe: **0.23.0**",
            f"  Worker: **{d.get('version', '?')}**",
            f"  Ollama: {'OK' if d.get('ollama') else 'FAIL'}",
            f"  DB: {'OK' if d.get('database') else 'FAIL'}",
            f"  Multi-track: {'ON' if d.get('multi_track') else 'OFF'} ({v.MULTI_TRACK_MODE})",
            "",
            f"  **Track 1 (Local):** {'ON' if v.TRACK1_ENABLED else 'OFF'}",
            f"    M0/M2: `{v.MODEL_DISPATCHER}`",
            f"    M3: `{v.MODEL_STRATEGIST}`",
            f"    M4: `{v.MODEL_WRITER}`",
            f"    M5: `{v.MODEL_REVIEWER}`",
            f"    M6: `{v.MODEL_TRANSLATOR}`",
        ]
        for tid, prefix in [(2, "T2"), (3, "T3"), (4, "T4")]:
            enabled = getattr(v, f"TRACK{tid}_ENABLED", False)
            if enabled:
                url = getattr(v, f"TRACK{tid}_URL", "")
                writer = getattr(v, f"{prefix}_WRITER", "")
                chat = getattr(v, f"{prefix}_CHAT", "")
                lines.append("")
                lines.append(f"  **Track {tid} (API):** ON")
                lines.append(f"    URL: `{url[:50]}`")
                if writer:
                    lines.append(f"    Writer: `{writer}`")
                if chat:
                    lines.append(f"    Chat: `{chat}`")
        lines.append("")
        lines.append(f"  **AI Chat:** {'ON' if v.ENABLE_AI_CHAT else 'OFF'}")
        lines.append(f"  **Live stream:** {'ON' if v.ENABLE_LIVE_STREAM else 'OFF'}")
        return N.join(lines)

    def _cmd_health(self, w):
        import requests
        N = chr(10)
        lines = [chr(127973) + " **Health Check**", ""]
        checks = [
            ("Worker", w + "/health"),
            ("GPU", w + "/gpu-status"),
            ("Tracki", w + "/tracks"),
            ("WordPress", w + "/wp-test")
        ]
        for name, url in checks:
            try:
                r = requests.get(url, timeout=10)
                if r.status_code == 200:
                    lines.append(f"  {chr(9989)} {name}: OK")
                else:
                    lines.append(f"  {chr(10060)} {name}: HTTP {r.status_code}")
            except Exception as e:
                lines.append(f"  {chr(10060)} {name}: {str(e)[:50]}")
        return N.join(lines)

    # ═══════════════════════════════════════════
    # M1: SCANNER
    # ═══════════════════════════════════════════

    def _cmd_inbox(self, w):
        d = self._get(w, "/inbox")
        if self._err(d):
            return self._err_msg(d, "inbox")
        N = chr(10)
        files = d.get("files", [])
        if not files:
            return chr(128193) + " Inbox pusty. Skopiuj pliki do `/data/inbox/`"
        lines = [chr(128193) + f" **Inbox** ({d.get('count', len(files))} plików)", ""]
        for f in files[:30]:
            size_kb = f.get("size", 0) // 1024
            lines.append(f"  {chr(128196)} `{f.get('filename', '?')}` ({size_kb} KB)")
        if len(files) > 30:
            lines.append(f"  _...i {len(files) - 30} więcej_")
        return N.join(lines)

    def _cmd_dokumenty(self, w):
        d = self._get(w, "/materials")
        if self._err(d):
            return self._err_msg(d, "dokumenty")
        N = chr(10)
        mats = d.get("materials", []) if isinstance(d, dict) else d
        if not mats:
            return chr(128193) + " Brak dokumentów. Wpisz `skanuj`."
        lines = [chr(128193) + f" **Dokumenty** ({len(mats)})", ""]
        for m in mats[:30]:
            lines.append(f"  {chr(8226)} #{m.get('id', '?')} `{m.get('filename', '?')}` [{m.get('status', '?')}]")
        if len(mats) > 30:
            lines.append(f"  _...i {len(mats) - 30} więcej_")
        return N.join(lines)

    def _cmd_dokument(self, w, doc_id, full=False):
        path = f"/document/{doc_id}" + ("?full=1" if full else "")
        d = self._get(w, path)
        if self._err(d):
            return self._err_msg(d, f"dokument {doc_id}")
        N = chr(10)
        lines = [
            chr(128196) + f" **Dokument #{doc_id}**", "",
            f"  Plik: `{d.get('filename', '?')}`",
            f"  Typ: {d.get('file_type', '?')}",
            f"  Rozmiar: {d.get('file_size', 0) // 1024} KB",
            f"  Znaki: {d.get('char_count', 0)}",
            f"  Status: {d.get('status', '?')}",
        ]
        if d.get("classification"):
            c = d["classification"]
            lines += ["", chr(127991) + chr(65039) + " **Klasyfikacja:**",
                       f"  Projekt: {c.get('project', '?')}",
                       f"  Kategoria: {c.get('category', '?')}",
                       f"  Przydatność: {c.get('usefulness', '?')}/10",
                       f"  Podsumowanie: _{c.get('summary', '')[:200]}_"]
        if full and d.get("content"):
            lines += ["", "---", "", d.get("content", "")]
        return N.join(lines)

    def _cmd_duplikaty(self, w):
        d = self._get(w, "/duplicates")
        if self._err(d):
            return self._err_msg(d, "duplikaty")
        N = chr(10)
        dups = d.get("duplicates", [])
        if not dups:
            return chr(9989) + " Brak duplikatów."
        lines = [chr(128257) + f" **Duplikaty** ({len(dups)})", ""]
        for dup in dups[:20]:
            lines.append(f"  {chr(8226)} {dup.get('filenames', '?')} (x{dup.get('count', 0)})")
        return N.join(lines)

    def _cmd_usun_dokument(self, w, doc_id):
        d = self._post(w, f"/document/{doc_id}/delete")
        if self._err(d):
            return self._err_msg(d, f"usun dokument {doc_id}")
        return chr(9989) + f" Dokument #{doc_id} usunięty."

    def _cmd_failed(self, w):
        d = self._get(w, "/failed")
        if self._err(d):
            return self._err_msg(d, "failed")
        N = chr(10)
        cats = d.get("categories", {})
        if not cats:
            return chr(9989) + " Brak plików z błędami."
        lines = [chr(9888) + chr(65039) + " **Pliki z błędami**", ""]
        for cat, info in cats.items():
            lines.append(f"  {chr(128194)} **{cat}** ({info.get('count', 0)})")
            for f in info.get("files", [])[:5]:
                lines.append(f"    {chr(8226)} `{f}`")
        return N.join(lines)

    def _cmd_failed_retry(self, w):
        d = self._post(w, "/failed/retry")
        if self._err(d):
            return self._err_msg(d, "failed retry")
        return chr(9989) + f" Przeniesiono {d.get('moved', 0)} plików do inbox."

    # ═══════════════════════════════════════════
    # M2: CLASSIFIER
    # ═══════════════════════════════════════════

    def _cmd_klasyfikacja(self, w, doc_id):
        d = self._get(w, f"/classification/{doc_id}")
        if self._err(d):
            return self._err_msg(d, f"klasyfikacja {doc_id}")
        N = chr(10)
        return N.join([
            chr(127991) + chr(65039) + f" **Klasyfikacja #{doc_id}**", "",
            f"  Projekt: **{d.get('project', '?')}**",
            f"  Kategoria: {d.get('category', '?')}",
            f"  Przydatność: {d.get('usefulness', '?')}/10",
            f"  Tagi: {', '.join(d.get('tags', []))}",
            "", f"  _{d.get('summary', '')}_",
        ])

    def _cmd_projects(self, w):
        d = self._get(w, "/projects")
        if self._err(d):
            return self._err_msg(d, "projekty")
        N = chr(10)
        prj = d.get("projects", []) if isinstance(d, dict) else d
        if not prj:
            return chr(128193) + " Brak projektów. Wrzuć pliki i `skanuj` + `klasyfikuj`."
        lines = [chr(128193) + " **Projekty:**", ""]
        for p in prj:
            cnt = p.get("doc_count", p.get("document_count", 0))
            avg = round(p.get("avg_usefulness", 0), 1)
            lines.append(f"  {chr(128194)} **{p.get('name', '?')}** ({cnt} dok., avg {avg}/10)")
        return N.join(lines)

    def _cmd_materials(self, w, raw):
        params = {}
        for kw in ["z projektu ", "projekt ", "o "]:
            if kw in raw.lower():
                idx = raw.lower().index(kw) + len(kw)
                params["project"] = raw[idx:].strip()
                break
        path = "/materials"
        if "project" in params:
            path += f"?project={params['project']}"
        d = self._get(w, path)
        if self._err(d):
            return self._err_msg(d, "materialy")
        N = chr(10)
        mats = d.get("materials", []) if isinstance(d, dict) else d
        if not mats:
            return chr(128193) + " Brak materiałów."
        lines = [chr(128193) + f" **Materiały** ({len(mats)})", ""]
        cur = ""
        for m in mats[:25]:
            proj = m.get("project", "")
            if proj != cur:
                cur = proj
                lines += ["", chr(128194) + f" **{cur}**"]
            lines.append(f"  {chr(8226)} `{m.get('filename', '?')}` ({m.get('category', '')}, {m.get('usefulness', '?')}/10)")
            s = m.get("summary", "")
            if s:
                lines.append(f"    _{s[:80]}_")
        return N.join(lines)

    def _cmd_search(self, w, query):
        d = self._get(w, f"/search?q={query}")
        if self._err(d):
            return self._err_msg(d, "szukaj")
        N = chr(10)
        res = d.get("results", []) if isinstance(d, dict) else d
        if not res:
            return chr(128269) + f" Brak wyników dla: `{query}`"
        lines = [chr(128269) + f" **Wyniki: `{query}`**", ""]
        for r in res[:15]:
            lines.append(f"  {chr(8226)} `{r.get('filename', '?')}` — {r.get('project', '?')} ({r.get('usefulness', '?')}/10)")
        return N.join(lines)

    def _cmd_tagi(self, w):
        d = self._get(w, "/tags")
        if self._err(d):
            return self._err_msg(d, "tagi")
        N = chr(10)
        tags = d.get("tags", [])
        if not tags:
            return chr(127991) + chr(65039) + " Brak tagów."
        lines = [chr(127991) + chr(65039) + " **Tagi:**", ""]
        for t in tags[:30]:
            lines.append(f"  {chr(8226)} `{t.get('name', '?')}` ({t.get('count', 0)})")
        return N.join(lines)

    def _cmd_przydatnosc(self, w, min_u):
        d = self._get(w, f"/materials?min_usefulness={min_u}")
        if self._err(d):
            return self._err_msg(d, "przydatnosc")
        N = chr(10)
        mats = d.get("materials", [])
        return chr(128200) + f" Materiały z przydatnością >= {min_u}: **{len(mats)}**"

    # ═══════════════════════════════════════════
    # M3: STRATEGIST
    # ═══════════════════════════════════════════

    def _cmd_zaplanuj(self, w, cmd, raw):
        payload = {}
        if "--krotki" in cmd or "--short" in cmd:
            payload["target_words"] = 600
        elif "--dlugi" in cmd or "--long" in cmd:
            payload["target_words"] = 2000
        if "--seria" in cmd:
            payload["series"] = True
        proj = _re.sub(r'--\w+', '', raw[9:]).strip()
        if proj:
            payload["project"] = proj
        return self._stream(w, "/plan", "M3: Planowanie", payload)

    def _cmd_briefs(self, w):
        d = self._get(w, "/briefs")
        if self._err(d):
            return self._err_msg(d, "briefy")
        N = chr(10)
        brs = d.get("briefs", []) if isinstance(d, dict) else d
        if not brs:
            return chr(128203) + " Brak briefów. Wpisz `zaplanuj`."
        lines = [chr(128203) + " **Briefy:**", ""]
        for b in brs:
            ico = chr(9989) if b.get("status") == "done" else chr(128203)
            lines.append(f"  {ico} #{b.get('id', '?')} **{b.get('title', '?')}** [{b.get('status', '?')}]")
        return N.join(lines)

    def _cmd_brief_detail(self, w, bid):
        d = self._get(w, f"/brief/{bid}")
        if self._err(d):
            return self._err_msg(d, f"brief {bid}")
        N = chr(10)
        lines = [
            chr(128203) + f" **Brief #{bid}**", "",
            f"  Tytuł: **{d.get('title', '?')}**",
            f"  Temat: {d.get('topic', '?')}",
            f"  Status: {d.get('status', '?')}",
            f"  Cel: ~{d.get('target_words', 1200)} słów",
            f"  Kategoria WP: {d.get('wp_category', '?')}",
        ]
        structure = d.get("structure", [])
        if structure:
            lines += ["", "  **Struktura:**"]
            for s in structure[:6]:
                lines.append(f"    ## {s.get('heading', '?')}")
        return N.join(lines)

    def _cmd_usun_brief(self, w, brief_id):
        d = self._post(w, f"/brief/{brief_id}/delete")
        if self._err(d):
            return self._err_msg(d, f"usun brief {brief_id}")
        return chr(9989) + f" Brief #{brief_id} usunięty."

    # ═══════════════════════════════════════════
    # M4: WRITER
    # ═══════════════════════════════════════════

    def _cmd_napisz(self, w, cmd, raw):
        payload = {"live_stream": self.valves.ENABLE_LIVE_STREAM}
        if "--bez-korekty" in cmd or "--no-review" in cmd:
            payload["skip_review"] = True
        if "--bez-tlumaczenia" in cmd or "--no-translate" in cmd:
            payload["skip_translate"] = True
        if "--krotki" in cmd or "--short" in cmd:
            payload["target_words"] = 700
        if "--dlugi" in cmd or "--long" in cmd:
            payload["target_words"] = 2000
        m = _re.search(r'napisz\s+(\d+)', cmd)
        if m:
            payload["brief_id"] = int(m.group(1))
        label = "M4: Pisanie"
        if not payload.get("skip_review"):
            label += " + M5: Korekta"
        if not payload.get("skip_translate"):
            label += " + M6: Tłumaczenie"
        return self._stream(w, "/write", label, payload)

    def _cmd_articles(self, w):
        d = self._get(w, "/articles")
        if self._err(d):
            return self._err_msg(d, "artykuly")
        N = chr(10)
        arts = d.get("articles", []) if isinstance(d, dict) else d
        if not arts:
            return chr(128221) + " Brak artykułów. Wpisz `napisz`."
        lines = [chr(128221) + " **Artykuły:**", ""]
        for a in arts:
            st = a.get("status", "?")
            ico = self._ico(st)
            title = a.get("title_pl") or a.get("title_en", "?")
            line = f"  {ico} #{a.get('id', '?')} **{title}** [{st}]"
            wp = a.get("wp_post_id")
            if wp:
                line += f" Post #{wp}"
            lines.append(line)
        return N.join(lines)

    def _cmd_article_en(self, w, aid):
        d = self._get(w, f"/preview?id={aid}")
        if self._err(d):
            return self._err_msg(d, f"artykul en {aid}")
        N = chr(10)
        content = d.get("content_en", "Brak treści EN")
        title = d.get("title_en", "?")
        return N.join([chr(127468) + chr(127463) + f" **{title}** (EN)", "", "---", "", content])

    # ═══════════════════════════════════════════
    # M5: REVIEWER
    # ═══════════════════════════════════════════

    def _cmd_koryguj(self, w, cmd):
        payload = {"only_review": True, "live_stream": self.valves.ENABLE_LIVE_STREAM}
        m = _re.search(r'(\d+)', cmd)
        if m:
            payload["article_id"] = int(m.group(1))
        if "--tylko-kod" in cmd:
            payload["code_only"] = True
        if "--surowo" in cmd:
            payload["quality_threshold"] = 9
        return self._stream(w, "/write", "M5: Korekta techniczna", payload)

    def _cmd_korekta(self, w, aid):
        d = self._get(w, f"/review/{aid}")
        if self._err(d):
            return self._err_msg(d, f"korekta {aid}")
        N = chr(10)
        issues = d.get("issues", [])
        lines = [
            chr(128270) + f" **Korekta #{aid}**", "",
            f"  Jakość: **{d.get('review_score', '?')}/10**",
            f"  Model: {d.get('reviewer_model', '?')}",
            f"  Issues: {len(issues)}",
        ]
        for i in issues[:5]:
            sev = chr(128308) if i.get("severity") == "HIGH" else chr(128993)
            lines.append(f"    {sev} {i.get('description', '')}")
        return N.join(lines)

    def _cmd_issues(self, w, aid):
        d = self._get(w, f"/review/{aid}/issues")
        if self._err(d):
            return self._err_msg(d, f"issues {aid}")
        N = chr(10)
        issues = d.get("issues", [])
        lines = [
            chr(128270) + f" **Issues #{aid}**", "",
            f"  HIGH: {d.get('high', 0)}",
            f"  LOW: {d.get('low', 0)}", ""
        ]
        for i in issues:
            sev = chr(128308) if i.get("severity") == "HIGH" else chr(128993)
            lines.append(f"  {sev} {i.get('description', '')}")
            if i.get("fix"):
                lines.append(f"    {chr(8594)} {i.get('fix')}")
        return N.join(lines)

    def _cmd_zatwierdz(self, w, aid):
        d = self._post(w, f"/article/{aid}/approve")
        if self._err(d):
            return self._err_msg(d, f"zatwierdz {aid}")
        return chr(9989) + f" Artykuł #{aid} zatwierdzony."

    # ═══════════════════════════════════════════
    # M6: TRANSLATOR
    # ═══════════════════════════════════════════

    def _cmd_przetlumacz(self, w, cmd, raw):
        payload = {"only_translate": True, "live_stream": self.valves.ENABLE_LIVE_STREAM}
        m = _re.search(r'przetlumacz\s+(\d+)', cmd) or _re.search(r'przetłumacz\s+(\d+)', cmd)
        if m:
            payload["article_id"] = int(m.group(1))
        if "--formalnie" in cmd:
            payload["style"] = "formal"
        if "--nieformalnie" in cmd:
            payload["style"] = "informal"
        if "ponownie" in cmd or "--force" in cmd:
            payload["force"] = True
        return self._stream(w, "/write", "M6: Tłumaczenie EN→PL", payload)

    def _cmd_preview(self, w):
        d = self._get(w, "/preview")
        if self._err(d):
            return self._err_msg(d, "podglad")
        N = chr(10)
        if not d:
            return chr(128221) + " Brak artykułu. Wpisz `napisz`."
        content = d.get("content_pl") or d.get("content_en") or "Brak treści"
        title = d.get("title_pl") or d.get("title_en") or "?"
        status = d.get("status", "?")
        url = d.get("wp_post_url", "")
        lines = [chr(128221) + f" **{title}** [{status}]"]
        if url:
            lines.append(chr(128279) + f" {url}")
        lines += ["", "---", "", content]
        return N.join(lines)

    def _cmd_preview_id(self, w, aid):
        d = self._get(w, f"/preview?id={aid}")
        if self._err(d):
            return self._err_msg(d, f"podglad {aid}")
        N = chr(10)
        content = d.get("content_pl") or d.get("content_en") or "Brak"
        title = d.get("title_pl") or d.get("title_en") or "?"
        return N.join([chr(128221) + f" **{title}**", "", "---", "", content])

    def _cmd_porownaj(self, w, aid):
        d = self._get(w, f"/preview?id={aid}")
        if self._err(d):
            return self._err_msg(d, f"porownaj {aid}")
        N = chr(10)
        en = d.get("content_en", "Brak EN")
        pl = d.get("content_pl", "Brak PL")
        t_en = d.get("title_en", "?")
        t_pl = d.get("title_pl", "?")
        return N.join([
            chr(127468) + chr(127463) + f" **{t_en}**", "", "---", "",
            en[:1500], "", "",
            chr(127477) + chr(127473) + f" **{t_pl}**", "", "---", "",
            pl[:1500]
        ])

    def _cmd_wyjatki(self, w):
        d = self._get(w, "/exceptions")
        if self._err(d):
            return self._err_msg(d, "wyjatki")
        N = chr(10)
        exc = d.get("exceptions", [])
        return N.join([
            chr(128220) + f" **Wyjątki tłumaczenia** ({d.get('count', len(exc))})", "",
            ", ".join(f"`{e}`" for e in exc[:50])
        ])

    def _cmd_wyjatki_dodaj(self, w, word):
        d = self._post(w, "/exceptions/add", {"word": word})
        if self._err(d):
            return self._err_msg(d, "wyjatki dodaj")
        return chr(9989) + f" Dodano wyjątek: `{word}`"

    def _cmd_wyjatki_usun(self, w, word):
        d = self._post(w, "/exceptions/remove", {"word": word})
        if self._err(d):
            return self._err_msg(d, "wyjatki usun")
        return chr(9989) + f" Usunięto wyjątek: `{word}`"

    # ═══════════════════════════════════════════
    # M7: PUBLISHER
    # ═══════════════════════════════════════════

    def _cmd_publish(self, w, aid=None):
        import requests
        payload = self._wp_data()
        if aid:
            payload["article_id"] = aid
        try:
            r = requests.post(w + "/publish", json=payload, timeout=30)
            res = r.json()
            N = chr(10)
            if res.get("event") == "published":
                return N.join([
                    chr(9989) + " **Opublikowano jako DRAFT!**",
                    f"  {chr(128221)} Post #{res.get('wp_post_id', '')}",
                    f"  {chr(127991) + chr(65039)} Kategoria: {res.get('category', '')}",
                    f"  {chr(128279)} {res.get('wp_post_url', '')}",
                    "", "Sprawdź w WordPress Admin i opublikuj."
                ])
            return chr(10060) + " " + res.get("message", "Błąd publikacji")
        except Exception as e:
            return chr(10060) + f" Błąd: {e}"

    def _cmd_aktualizuj(self, w, aid):
        d = self._post(w, "/publish/update", {"article_id": aid})
        if self._err(d):
            return self._err_msg(d, f"aktualizuj {aid}")
        return chr(9989) + f" Post #{d.get('wp_post_id', '?')} zaktualizowany."

    def _cmd_wp_test(self, w):
        d = self._get(w, "/wp-test")
        if self._err(d):
            return self._err_msg(d, "wp test")
        N = chr(10)
        if d.get("auth_ok"):
            return N.join([
                chr(9989) + " **WordPress połączony!**",
                f"  {chr(128100)} {d.get('user', '?')}",
                f"  {chr(128279)} {d.get('wp_url', '?')}",
                f"  {chr(128274)} Challenge: {'OK' if d.get('challenge_solved') else 'FAIL'}"
            ])
        return N.join([
            chr(9888) + chr(65039) + " **WordPress problem:**",
            f"  Challenge: {'OK' if d.get('challenge_solved') else 'FAIL'}",
            f"  API: {'OK' if d.get('api_accessible') else 'FAIL'}",
            f"  Auth: {'OK' if d.get('auth_ok') else 'FAIL'}",
            f"  Error: {str(d.get('error', ''))[:150]}"
        ])

    def _cmd_wp_config(self):
        v = self.valves
        N = chr(10)
        hp = chr(9989) + " ustawione" if v.WP_APP_PASSWORD else chr(10060) + " BRAK"
        return N.join([
            chr(128295) + " **WordPress Config**", "",
            f"  {chr(128279)} URL: `{v.WP_URL}`",
            f"  {chr(128100)} User: `{v.WP_USER}`",
            f"  {chr(128273)} Password: {hp}",
            "", f"Zmień w: **Settings {chr(8594)} Valves**"
        ])

    def _cmd_wp_posts(self, w):
        d = self._get(w, "/articles")
        if self._err(d):
            return self._err_msg(d, "wp posty")
        N = chr(10)
        arts = d.get("articles", []) if isinstance(d, dict) else d
        pub = [a for a in arts if a.get("wp_post_id")]
        if not pub:
            return chr(127760) + " Brak opublikowanych postów."
        lines = [chr(127760) + " **Posty WordPress:**", ""]
        for a in pub:
            title = a.get("title_pl") or a.get("title_en", "?")
            lines.append(f"  {chr(8226)} #{a.get('wp_post_id', '?')} **{title}** [{a.get('status', '?')}]")
            url = a.get("wp_post_url", "")
            if url:
                lines.append(f"    {chr(128279)} {url}")
        return N.join(lines)

    def _cmd_wp_kategorie(self, w):
        d = self._get(w, "/wp-categories")
        if self._err(d):
            return self._err_msg(d, "wp kategorie")
        N = chr(10)
        cats = d.get("categories", [])
        lines = [chr(127991) + chr(65039) + " **Kategorie WP:**", ""]
        for c in cats:
            lines.append(f"  {chr(8226)} {c.get('name', '?')} (ID: {c.get('id', '?')}, {c.get('count', 0)} postów)")
        return N.join(lines)

    def _cmd_wp_tagi(self, w):
        d = self._get(w, "/wp-tags")
        if self._err(d):
            return self._err_msg(d, "wp tagi")
        N = chr(10)
        tags = d.get("tags", [])
        lines = [chr(127991) + chr(65039) + " **Tagi WP:**", ""]
        for t in tags[:30]:
            lines.append(f"  {chr(8226)} {t.get('name', '?')} ({t.get('count', 0)})")
        return N.join(lines)

    def _cmd_wp_post(self, w, post_id):
        d = self._get(w, f"/wp-post/{post_id}")
        if self._err(d):
            return self._err_msg(d, f"wp post {post_id}")
        N = chr(10)
        return N.join([
            chr(128221) + f" **WP Post #{post_id}**", "",
            f"  Tytuł: {d.get('title', {}).get('rendered', '?')}",
            f"  Status: {d.get('status', '?')}",
            f"  Link: {d.get('link', '?')}"
        ])

    def _cmd_synchronizuj(self, w):
        d = self._post(w, "/wp-sync")
        if self._err(d):
            return self._err_msg(d, "synchronizuj")
        return chr(9989) + f" Zsynchronizowano {d.get('synced', 0)} artykułów."

    # ═══════════════════════════════════════════
    # GLOBAL PIPELINE
    # ═══════════════════════════════════════════

    def _cmd_full_pipeline(self, w, cmd, raw):
        payload = self._wp_data()
        payload["live_stream"] = self.valves.ENABLE_LIVE_STREAM
        if "--bez-korekty" in cmd:
            payload["skip_review"] = True
        if "--tylko-en" in cmd:
            payload["skip_translate"] = True
            payload["skip_publish"] = True
        if "--bez-publikacji" in cmd:
            payload["skip_publish"] = True
        if "--fast" in cmd:
            payload["prefer_api"] = True
        proj = _re.sub(r'(pelny|pełny|full|pipeline|zrob|zrób|wszystko|caly|cały|proces|--\S+)', '', cmd).strip()
        if proj:
            payload["project"] = proj
        return self._stream(w, "/full-pipeline", "Pipeline M1→M7", payload)

    def _cmd_stop_pipeline(self, w):
        d = self._post(w, "/pipeline/stop")
        if self._err(d):
            return self._err_msg(d, "zatrzymaj pipeline")
        return chr(9989) + f" {d.get('message', 'Żądanie wysłane')}"

    def _cmd_gpu_free(self, w):
        d = self._post(w, "/gpu-free")
        if self._err(d):
            return self._err_msg(d, "zwolnij gpu")
        return chr(129529) + " GPU zwolniony."

    def _cmd_kolejka(self, w):
        d = self._get(w, "/queue")
        if self._err(d):
            return self._err_msg(d, "kolejka")
        N = chr(10)
        return N.join([
            chr(128203) + " **Kolejka**", "",
            f"  Running: {d.get('running', False)}",
            f"  Phase: {d.get('current_phase', '-')}",
            f"  Queue: {len(d.get('queue', []))}"
        ])

    def _cmd_historia(self, w):
        d = self._get(w, "/history")
        if self._err(d):
            return self._err_msg(d, "historia")
        N = chr(10)
        hist = d.get("history", [])
        if not hist:
            return chr(128203) + " Brak historii."
        lines = [chr(128203) + f" **Historia** ({d.get('total', len(hist))})", ""]
        for h in hist[:20]:
            ts = h.get("timestamp", "?")[:16]
            lines.append(f"  {chr(8226)} `{ts}` {h.get('action', '?')}")
        return N.join(lines)

    def _cmd_logi(self, w, n):
        d = self._get(w, f"/logs?n={n}")
        if self._err(d):
            return self._err_msg(d, "logi")
        N = chr(10)
        lines = d.get("lines", [])
        if not lines:
            return chr(128203) + " Brak logów."
        return N.join([chr(128203) + f" **Logi** (ostatnie {len(lines)})", "", "```"] + lines + ["```"])

    def _cmd_export(self, w, aid, lang="pl"):
        path = "/preview"
        if aid:
            path += f"?id={aid}"
        d = self._get(w, path)
        if self._err(d):
            return self._err_msg(d, "eksport")
        N = chr(10)
        if lang == "en":
            content = d.get("content_en", "")
            title = d.get("title_en", "Artykuł")
        else:
            content = d.get("content_pl") or d.get("content_en", "")
            title = d.get("title_pl") or d.get("title_en", "Artykuł")
        if not content:
            return "Brak artykułu do eksportu."
        return N.join([
            chr(128230) + f" **Eksport: {title}**", "",
            "```markdown", f"# {title}", "", content, "```"
        ])

    def _cmd_backup(self, w):
        d = self._post(w, "/backup")
        if self._err(d):
            return self._err_msg(d, "backup")
        return chr(9989) + f" Backup utworzony: `{d.get('backup_file', '?')}`"

    # ═══════════════════════════════════════════
    # UWAGI
    # ═══════════════════════════════════════════

    def _cmd_uwaga(self, w, raw):
        import requests
        N = chr(10)
        try:
            r = requests.post(w + "/modify", json={"message": raw}, timeout=120)
            res = r.json()
            if res.get("error"):
                return chr(10060) + f" {res['error']}"
            wc = res.get("word_count", "?")
            return N.join([
                chr(9989) + " **Uwaga zastosowana**",
                f"  Słowa: {wc}", "",
                "Wpisz `podglad` żeby zobaczyć wynik."
            ])
        except Exception as e:
            return chr(10060) + f" Błąd uwagi: {e}"

    # ═══════════════════════════════════════════
    # AI CHAT
    # ═══════════════════════════════════════════

    def _chat(self, w, msg):
        N = chr(10)
        if not self.valves.ENABLE_AI_CHAT:
            return N.join([
                chr(10067) + f" Nieznana komenda: `{msg[:50]}`", "",
                "Wpisz `pomoc` aby zobaczyć komendy.", "",
                "_AI chat wyłączony. Włącz w Valves._"
            ])
        import requests
        try:
            r = requests.post(w + "/chat", json={"message": msg}, timeout=120)
            return r.json().get("response", "Brak odpowiedzi. Wpisz `pomoc`.")
        except Exception:
            return "Worker niedostępny. Wpisz `pomoc`."

    # ═══════════════════════════════════════════
    # STREAMING (z live writing)
    # ═══════════════════════════════════════════

    def _stream(self, w, endpoint, label, extra_payload=None):
        """NDJSON stream z obsługą live writing."""
        import requests
        N = chr(10)
        yield chr(128640) + f" **{label}**{N}{N}"
        
        payload = {"live_stream": self.valves.ENABLE_LIVE_STREAM}
        if extra_payload:
            payload.update(extra_payload)
        
        try:
            r = requests.post(w + endpoint, json=payload, timeout=1800, stream=True)
            in_stream = False
            
            for line in r.iter_lines():
                if line:
                    try:
                        ev = json.loads(line)
                        event_type = ev.get("event", "")

                        # ── Live streaming: raw text chunks ──
                        if event_type == "stream_start":
                            in_stream = True
                            phase = ev.get("phase", "")
                            yield N + f"---{N}{N}"
                        elif event_type == "stream_chunk" and in_stream:
                            chunk = ev.get("chunk", "")
                            if chunk:
                                yield chunk
                        elif event_type == "stream_end":
                            in_stream = False
                            yield f"{N}{N}---{N}{N}"

                        # ── Track info ──
                        elif event_type == "track_selected":
                            track = ev.get("track", "?")
                            ttype = ev.get("type", "?")
                            model = ev.get("model", "?")
                            phase = ev.get("phase", "")
                            type_ico = self._track_ico(ttype)
                            yield f"  {chr(128204)} Track {track} {type_ico} {chr(8594)} `{model}`{N}"

                        # ── Standard events ──
                        else:
                            f = self._fmt(ev)
                            if f:
                                yield f + N
                    except json.JSONDecodeError:
                        pass
        except Exception as e:
            yield N + chr(10060) + f" **Błąd**: {e}" + N

    def _fmt(self, ev):
        """Formatuj event NDJSON → tekst Markdown."""
        N = chr(10)
        t = ev.get("event", "")

        if t == "phase":
            return chr(9203) + f" **[{ev.get('phase', '')}]** {ev.get('message', '')}"
        elif t == "model_loaded":
            return f"  {chr(129504)} Model: `{ev.get('model', '')}`"
        elif t == "scanned":
            return f"  {chr(9989)} `{ev.get('filename', '')}` ({ev.get('file_type', '')}, {ev.get('char_count', 0)} zn.)"
        elif t == "skipped":
            return f"  {chr(9197) + chr(65039)} `{ev.get('filename', '')}`: {ev.get('reason', '')}"
        elif t == "classified":
            line = f"  {chr(127991) + chr(65039)} `{ev.get('filename', '')}` {chr(8594)} **{ev.get('project', '')}** ({ev.get('usefulness', '')}/10)"
            s = ev.get("summary", "")
            if s:
                line += N + f"    _{s[:100]}_"
            return line
        elif t == "brief_created":
            txt = f"  {chr(128203)} **{ev.get('title', '')}** (źródeł: {ev.get('source_count', 0)})"
            for s in ev.get("structure", [])[:6]:
                txt += N + f"    ## {s.get('heading', '')}"
            return txt
        elif t == "materials_ready":
            return f"  {chr(9989)} Gotowe materiały: {ev.get('count', 0)} z projektu '{ev.get('project', '')}'"
        elif t == "progress":
            pct = ev.get("percent", 0)
            bf = pct // 10
            bar = chr(9619) * bf + chr(9617) * (10 - bf)
            extra = ""
            if "words" in ev:
                extra = f" | {ev['words']} słów"
            if "elapsed" in ev:
                extra += f" | {ev['elapsed']}s"
            return f"  [{bar}] {pct}%{extra}"
        elif t == "heartbeat":
            return f"  {chr(128147)} Pracuję... ({ev.get('message', '')})"
        elif t == "written":
            return f"  {chr(9989)} EN: **{ev.get('title_en', '')}** ({ev.get('word_count', 0)} słów, {ev.get('elapsed_seconds', 0)}s)"
        elif t == "reviewed":
            txt = f"  {chr(9989)} Jakość: **{ev.get('quality_score', '?')}/10**"
            for i in ev.get("issues", [])[:3]:
                sev = chr(128308) if i.get("severity") == "HIGH" else chr(128993)
                txt += N + f"    {sev} {i.get('description', '')}"
            return txt
        elif t == "translated":
            return f"  {chr(9989)} PL: **{ev.get('title_pl', '')}** ({ev.get('word_count', 0)} słów, {ev.get('elapsed_seconds', 0)}s)"
        elif t == "published":
            return f"  {chr(9989)} Post #{ev.get('wp_post_id', '')} {chr(8594)} DRAFT{N}  {chr(128279)} {ev.get('wp_post_url', '')}"
        elif t == "done":
            return chr(9989) + f" **{ev.get('phase', '')}** — {ev.get('message', '')}"
        elif t == "pipeline_done":
            return N + chr(127937) + f" **Pipeline zakończony!** ({ev.get('elapsed_seconds', 0)}s)"
        elif t == "pipeline_stopped":
            return chr(9888) + chr(65039) + f" {ev.get('message', '')}"
        elif t == "stopped":
            return chr(9888) + chr(65039) + f" {ev.get('message', '')}"
        elif t == "gpu_freed":
            return chr(129529) + " GPU zwolniony"
        elif t == "error":
            return chr(10060) + f" {ev.get('message', ev.get('error', ''))}"
        elif t in ("scan_empty", "classify_empty"):
            return chr(8505) + chr(65039) + f" {ev.get('message', '')}"
        elif t == "wait_for_more":
            return chr(9203) + f" {ev.get('message', '')}"
        return None

