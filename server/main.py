"""
server/main.py
--------------
Hub centrale del sistema TV Intercom.
Gestisce connessioni WebSocket degli operatori, stato del copione,
invio istruzioni audio ai canali camera e trigger manuali dalla regia.

Avvio:
    python -m uvicorn server.main:app --host 0.0.0.0 --port 8000 --reload
"""

import asyncio
import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Aggiungi i path necessari
sys.path.append(str(Path(__file__).parent.parent / "script-parser"))
sys.path.append(str(Path(__file__).parent))  # per tts_engine
from script_parser import load_script, get_auto_cues, get_manual_cues, Cue
from cue_engine import CueEngine, FiredCue
from tts_engine import TTSEngine

# API key ElevenLabs — impostala come variabile d'ambiente:
#   Windows: set ELEVENLABS_API_KEY=la-tua-key
import os
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")

# ---------------------------------------------------------------------------
# Logging — file con rotazione + console
# ---------------------------------------------------------------------------
from logging.handlers import RotatingFileHandler

LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

_log_format = "%(asctime)s [%(levelname)s] %(name)s — %(message)s"
_log_datefmt = "%Y-%m-%d %H:%M:%S"

# Root logger
logging.basicConfig(level=logging.INFO, format=_log_format, datefmt=_log_datefmt)

# File handler con rotazione (5 file × 5 MB = max 25 MB)
_file_handler = RotatingFileHandler(
    LOG_DIR / "intercom.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
)
_file_handler.setFormatter(logging.Formatter(_log_format, datefmt=_log_datefmt))
_file_handler.setLevel(logging.DEBUG)  # file cattura tutto, console solo INFO+
logging.getLogger().addHandler(_file_handler)

log = logging.getLogger("intercom")

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="TV Intercom Server", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Middleware: log tutte le richieste API con timing
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Skip log per static files e health checks
        path = request.url.path
        if path.startswith("/operator/") or path.startswith("/regia/"):
            return await call_next(request)
        t0 = time.time()
        response = await call_next(request)
        elapsed = (time.time() - t0) * 1000
        if elapsed > 100 or response.status_code >= 400:
            # Log solo richieste lente o con errore (DEBUG logga tutto)
            log.info(f"HTTP {request.method} {path} → {response.status_code} "
                     f"({elapsed:.0f}ms)")
        else:
            log.debug(f"HTTP {request.method} {path} → {response.status_code} "
                      f"({elapsed:.0f}ms)")
        return response

app.add_middleware(RequestLoggingMiddleware)

# ---------------------------------------------------------------------------
# Stato globale
# ---------------------------------------------------------------------------
class AppState:
    def __init__(self):
        self.script_loaded: bool = False
        self.metadata: dict = {}
        self.all_cues: list[Cue] = []
        self.script_lines: list[dict] = []   # blocchi testo+cue per il prompter
        self.engine: Optional[CueEngine] = None
        self.operator_connections: dict[int, WebSocket] = {}
        self.director_connections: list[WebSocket] = []
        self.last_audio: dict[int, bytes] = {}
        self.last_text: dict[int, dict] = {}
        self.start_time: float = time.time()
        self.cues_fired_count: int = 0
        self.tts: Optional[TTSEngine] = None
        self.stt_active: bool = False
        self.stt_device: Optional[int] = None
        self.stt_engine: str = "deepgram"
        # Code per camera — invio sequenziale, no crash su fire rapido
        self.camera_queues: dict[int, asyncio.Queue] = {}
        self.queue_workers: dict[int, asyncio.Task] = {}
        # Timeout connessioni — ultimo ping ricevuto per camera
        self.camera_last_ping: dict[int, float] = {}

CAMERA_PING_TIMEOUT = 15  # secondi senza ping → camera considerata morta

state = AppState()

# ---------------------------------------------------------------------------
# Caricamento copione all'avvio
# ---------------------------------------------------------------------------
SCRIPT_PATH = Path(__file__).parent.parent / "script-parser" / "sample_script.json"

@app.on_event("startup")
async def startup():
    log.info("=" * 60)
    log.info("TV Intercom Server — avvio")
    log.info(f"  Log directory: {LOG_DIR}")
    log.info(f"  Script path:   {SCRIPT_PATH}")
    log.info(f"  TTS:           {'ElevenLabs' if ELEVENLABS_API_KEY else 'DISABILITATO'}")
    log.info("=" * 60)

    # Inizializza TTS
    if ELEVENLABS_API_KEY:
        state.tts = TTSEngine(api_key=ELEVENLABS_API_KEY)
        log.info("TTS Engine inizializzato con ElevenLabs")
    else:
        log.warning("ELEVENLABS_API_KEY non impostata — TTS disabilitato, solo testo")

    if SCRIPT_PATH.exists():
        await load_script_file(str(SCRIPT_PATH))
        log.info(f"Copione caricato: {state.metadata.get('title', '?')}")
    else:
        log.warning(f"Nessun copione trovato in {SCRIPT_PATH}")

    # Avvia watchdog connessioni camera
    asyncio.create_task(_camera_watchdog())

# ---------------------------------------------------------------------------
# Helper: notifica la regia
# ---------------------------------------------------------------------------
async def notify_directors(event: dict):
    msg = json.dumps(event)
    dead = []
    for ws in state.director_connections:
        try:
            await ws.send_text(msg)
        except Exception as e:
            log.warning(f"Director WS morto, rimuovo: {e}")
            dead.append(ws)
    for ws in dead:
        state.director_connections.remove(ws)
    if dead:
        log.debug(f"Rimossi {len(dead)} director WS morti, "
                   f"restano {len(state.director_connections)}")

# ---------------------------------------------------------------------------
# Watchdog: rileva camere "fantasma" (connessione persa senza disconnect)
# ---------------------------------------------------------------------------
async def _camera_watchdog():
    """Controlla ogni 5s se qualche camera non manda ping da troppo tempo."""
    log.info(f"📡 Camera watchdog avviato (timeout={CAMERA_PING_TIMEOUT}s)")
    while True:
        await asyncio.sleep(5)
        now = time.time()
        dead_cams = []
        for cam_id, last_ping in list(state.camera_last_ping.items()):
            if cam_id not in state.operator_connections:
                continue
            elapsed = now - last_ping
            if elapsed > CAMERA_PING_TIMEOUT:
                dead_cams.append((cam_id, elapsed))

        for cam_id, elapsed in dead_cams:
            log.warning(f"📡 CAM{cam_id} timeout ({elapsed:.0f}s senza ping) "
                        f"— chiudo connessione fantasma")
            ws = state.operator_connections.pop(cam_id, None)
            state.camera_last_ping.pop(cam_id, None)
            _stop_camera_worker(cam_id)
            if ws:
                try:
                    await ws.close()
                except Exception:
                    pass
            await notify_directors({
                "type": "camera_disconnected",
                "camera": cam_id,
                "cameras_online": list(state.operator_connections.keys()),
            })

# ---------------------------------------------------------------------------
# Helper: recupera audio
# ---------------------------------------------------------------------------
async def _get_audio(instr) -> Optional[bytes]:
    t0 = time.time()
    # 1. File pre-registrato manuale (priorità massima)
    if instr.audio_file:
        p = Path(__file__).parent.parent / "script-parser" / instr.audio_file
        if p.exists():
            audio = p.read_bytes()
            log.debug(f"  audio file '{instr.audio_file}' letto ({len(audio)} bytes)")
            return audio
        log.warning(f"File audio non trovato: {instr.audio_file}")

    # 2. TTS ElevenLabs (dalla cache o generato al volo)
    if state.tts and instr.text:
        loop = asyncio.get_event_loop()
        audio = await loop.run_in_executor(None, state.tts.get_audio, instr.text)
        elapsed = (time.time() - t0) * 1000
        if audio:
            log.debug(f"  TTS audio per CAM{instr.camera} in {elapsed:.0f}ms "
                      f"({len(audio)} bytes)")
            return audio
        log.warning(f"  TTS fallito per CAM{instr.camera} dopo {elapsed:.0f}ms")

    # 3. Nessun audio disponibile — fallback testo
    log.info(f"  [no audio] CAM{instr.camera} fallback testo: '{instr.text[:60]}'")
    return None

# ---------------------------------------------------------------------------
# Helper: invia istruzione a una camera (audio o testo come fallback)
# ---------------------------------------------------------------------------
async def send_instruction_to_camera(instr, cue_id: str):
    ws = state.operator_connections.get(instr.camera)
    if not ws:
        log.warning(f"  CAM{instr.camera} non connessa — cue {cue_id} perso "
                    f"(connesse: {list(state.operator_connections.keys())})")
        return

    t0 = time.time()
    audio = await _get_audio(instr)

    try:
        if audio:
            # Salva per replay
            state.last_audio[instr.camera] = audio
            state.last_text[instr.camera] = {"text": instr.text, "cue_id": cue_id}
            header = json.dumps({
                "type": "instruction",
                "cue_id": cue_id,
                "camera": instr.camera,
                "text": instr.text,
            })
            await ws.send_text(header)
            await ws.send_bytes(audio)
            elapsed = (time.time() - t0) * 1000
            log.info(f"  → CAM{instr.camera}: audio {len(audio)}B in {elapsed:.0f}ms "
                     f"| cue={cue_id}")
        else:
            # Fallback: manda solo il testo
            state.last_text[instr.camera] = {"text": instr.text, "cue_id": cue_id}
            msg = json.dumps({
                "type": "instruction_text",
                "cue_id": cue_id,
                "camera": instr.camera,
                "text": instr.text,
            })
            await ws.send_text(msg)
            elapsed = (time.time() - t0) * 1000
            log.info(f"  → CAM{instr.camera}: solo testo in {elapsed:.0f}ms "
                     f"| cue={cue_id}")
    except Exception as e:
        elapsed = (time.time() - t0) * 1000
        log.error(f"  ❌ CAM{instr.camera} invio fallito dopo {elapsed:.0f}ms: {e}",
                  exc_info=True)
        state.operator_connections.pop(instr.camera, None)

# ---------------------------------------------------------------------------
# Code per camera — invio sequenziale per evitare crash su fire rapido
# ---------------------------------------------------------------------------
async def _camera_queue_worker(cam_id: int):
    """Processa istruzioni in coda per una camera — una alla volta."""
    queue = state.camera_queues.get(cam_id)
    if not queue:
        return
    log.debug(f"  Queue worker CAM{cam_id} avviato")
    while True:
        try:
            instr, cue_id = await queue.get()
            await send_instruction_to_camera(instr, cue_id)
            queue.task_done()
        except asyncio.CancelledError:
            log.info(f"  Queue worker CAM{cam_id} fermato")
            break
        except Exception as e:
            log.error(f"  Queue worker CAM{cam_id} errore: {e}", exc_info=True)


def _start_camera_worker(cam_id: int):
    """Avvia coda e worker per una camera — chiamato alla connessione WS."""
    if cam_id not in state.camera_queues:
        state.camera_queues[cam_id] = asyncio.Queue(maxsize=50)
    else:
        # Svuota la coda vecchia se la camera si riconnette
        q = state.camera_queues[cam_id]
        dropped = 0
        while not q.empty():
            try:
                q.get_nowait()
                dropped += 1
            except asyncio.QueueEmpty:
                break
        if dropped:
            log.warning(f"  CAM{cam_id} riconnessa: svuotati {dropped} item dalla coda")
    # Cancella worker precedente se esiste
    old_task = state.queue_workers.pop(cam_id, None)
    if old_task:
        old_task.cancel()
    state.queue_workers[cam_id] = asyncio.create_task(_camera_queue_worker(cam_id))
    log.debug(f"  Queue worker CAM{cam_id} (ri)avviato")


def _stop_camera_worker(cam_id: int):
    """Ferma worker e rimuove coda — chiamato alla disconnessione WS."""
    task = state.queue_workers.pop(cam_id, None)
    if task:
        task.cancel()
    state.camera_queues.pop(cam_id, None)
    log.debug(f"  Queue worker CAM{cam_id} fermato e rimosso")


async def _enqueue_instruction(instr, cue_id: str):
    """Accoda un'istruzione per una camera. Non blocca."""
    queue = state.camera_queues.get(instr.camera)
    if not queue:
        log.warning(f"  CAM{instr.camera} non ha coda — cue {cue_id} perso")
        return False
    try:
        queue.put_nowait((instr, cue_id))
        log.debug(f"  CAM{instr.camera} coda: +1 (pending={queue.qsize()}) | cue={cue_id}")
        return True
    except asyncio.QueueFull:
        log.error(f"  ❌ CAM{instr.camera} coda PIENA (50) — cue {cue_id} SCARTATO")
        return False

# ---------------------------------------------------------------------------
# Callback CueEngine → Dispatcher
# ---------------------------------------------------------------------------
def on_cue_fired(fc: FiredCue):
    state.cues_fired_count += 1
    cams = [i.camera for i in fc.cue.instructions]
    log.info(f"🔔 CUE FIRED: {fc.cue.cue_id} | conf={fc.confidence:.0%} | "
             f"cam={cams} | match='{fc.matched_text[:60]}'")
    asyncio.create_task(_dispatch_cue(fc))

async def _dispatch_cue(fc: FiredCue):
    t0 = time.time()
    cue_id = fc.cue.cue_id
    try:
        enqueued = 0
        dropped = 0
        for instr in fc.cue.instructions:
            ok = await _enqueue_instruction(instr, cue_id)
            if ok:
                enqueued += 1
            else:
                dropped += 1
        elapsed = (time.time() - t0) * 1000
        log.info(f"  ✅ dispatch {cue_id} accodato in {elapsed:.0f}ms | "
                 f"enqueued={enqueued} dropped={dropped}")
    except Exception as e:
        elapsed = (time.time() - t0) * 1000
        log.error(f"  ❌ dispatch {cue_id} FALLITO dopo {elapsed:.0f}ms: {e}",
                  exc_info=True)

    # Notifica regia (fuori dal try sopra per non perderla)
    try:
        await notify_directors({
            "type": "cue_fired",
            "cue_id": cue_id,
            "confidence": round(fc.confidence, 2),
            "cameras": [i.camera for i in fc.cue.instructions],
            "matched_text": fc.matched_text,
        })
    except Exception as e:
        log.error(f"  ❌ notify_directors per {cue_id} fallita: {e}", exc_info=True)

# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------

class LoadScriptRequest(BaseModel):
    path: str

async def load_script_file(path: str):
    log.info(f"📄 Caricamento copione: {path}")
    t0 = time.time()

    # --- Reset pulito dello stato precedente ---
    old_title = state.metadata.get("title", "")
    if state.script_loaded:
        log.info(f"  Reset stato precedente: '{old_title}'")
    if state.engine:
        state.engine.reset()
    state.cues_fired_count = 0
    state.last_text.clear()
    state.last_audio.clear()
    # Svuota code camera
    flushed = 0
    for cam_id, q in state.camera_queues.items():
        while not q.empty():
            try:
                q.get_nowait()
                flushed += 1
            except asyncio.QueueEmpty:
                break
    if flushed:
        log.info(f"  Code svuotate: {flushed} item residui")

    # --- Carica nuovo copione (parser unificato: entrambi i formati) ---
    meta, cues = load_script(path)
    state.metadata = meta
    state.all_cues = cues
    state.script_loaded = True
    auto = get_auto_cues(cues)
    manual = get_manual_cues(cues)
    state.engine = CueEngine(auto, on_cue_fired=on_cue_fired)

    # Carica script_lines per il prompter (se presenti nel JSON)
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        state.script_lines = raw.get("script_lines", [])
    except Exception:
        state.script_lines = []

    elapsed = (time.time() - t0) * 1000
    log.info(f"  Copione: '{meta.get('title','?')}' | "
             f"{len(cues)} cue totali ({len(auto)} auto, {len(manual)} manuali) | "
             f"script_lines: {len(state.script_lines)} | {elapsed:.0f}ms")
    # TTS pre-generazione NON automatica — usa /api/tts/pregenerate dalla dashboard

    return meta, cues

@app.post("/api/script/load")
async def api_load_script(req: LoadScriptRequest):
    try:
        meta, cues = await load_script_file(req.path)
        await notify_directors({"type": "script_loaded", "metadata": meta})
        return {"ok": True, "title": meta["title"], "total_cues": len(cues)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/scripts")
async def api_scripts():
    """Lista i file JSON copione disponibili nella cartella script-parser."""
    script_dir = Path(__file__).parent.parent / "script-parser"
    files = sorted([f.name for f in script_dir.glob("*.json")])
    return {"files": files}

@app.get("/api/script/download")
async def api_script_download(name: str = ""):
    """Scarica un file JSON. Se name specificato scarica quel file, altrimenti il copione attivo."""
    from fastapi.responses import FileResponse
    script_dir = Path(__file__).parent.parent / "script-parser"
    if name:
        p = script_dir / name
        if not p.exists() or p.suffix != ".json":
            raise HTTPException(status_code=404, detail=f"File {name} non trovato")
        return FileResponse(str(p), media_type="application/json", filename=name)
    if not state.script_loaded or not state.metadata:
        raise HTTPException(status_code=404, detail="Nessun copione attivo")
    title = state.metadata.get("title", "copione")
    safe  = re.sub(r'[^\w\s-]', '', title).strip()
    safe  = re.sub(r'[\s-]+', '_', safe).lower() or "copione"
    p = script_dir / f"{safe}.json"
    if not p.exists():
        p = script_dir / "script.json"
    if not p.exists():
        raise HTTPException(status_code=404, detail="File non trovato su disco")
    return FileResponse(str(p), media_type="application/json", filename=f"{safe}.json")

@app.delete("/api/script/file")
async def api_script_file_delete(name: str):
    """Elimina un singolo file JSON copione dalla libreria."""
    script_dir  = Path(__file__).parent.parent / "script-parser"
    script_path = script_dir / name
    if not script_path.exists() or script_path.suffix != ".json":
        raise HTTPException(status_code=404, detail=f"File {name} non trovato")
    script_path.unlink()
    log.info(f"File copione eliminato: {name}")
    if state.script_loaded:
        title = state.metadata.get("title", "")
        safe  = re.sub(r'[^\w\s-]', '', title).strip()
        safe  = re.sub(r'[\s-]+', '_', safe).lower()
        if name in (f"{safe}.json", "script.json"):
            state.script_loaded = False
            state.metadata      = {}
            state.all_cues      = []
            state.script_lines  = []
            state.engine        = None
            await notify_directors({"type": "script_cleared"})
    return {"ok": True, "deleted": name}

@app.post("/api/script/upload-file")
async def api_script_upload_file(file: UploadFile = File(...)):
    """Riceve un file JSON dal browser, lo salva e lo carica nel server."""
    log.info(f"📄 Upload file: {file.filename}")
    try:
        content = await file.read()
        script = json.loads(content)

        # Salva il file
        script_dir = Path(__file__).parent.parent / "script-parser"
        # Usa il nome originale del file se è un .json, altrimenti script.json
        filename = file.filename if file.filename and file.filename.endswith(".json") else "script.json"
        script_path = script_dir / filename
        script_path.write_bytes(content)
        log.info(f"  Salvato: {script_path}")

        # Carica con il parser unificato (gestisce entrambi i formati)
        meta, cues = await load_script_file(str(script_path))

        auto   = get_auto_cues(state.all_cues)
        manual = get_manual_cues(state.all_cues)

        await notify_directors({"type": "script_loaded", "metadata": state.metadata})
        return {
            "ok": True,
            "title": state.metadata.get("title", ""),
            "cues_total": len(state.all_cues),
            "cues_auto": len(auto),
            "cues_manual": len(manual),
        }
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="File non valido — deve essere un JSON")
    except Exception as e:
        log.exception("Errore upload script")
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/status")
async def api_status():
    auto = get_auto_cues(state.all_cues) if state.script_loaded else []
    manual = get_manual_cues(state.all_cues) if state.script_loaded else []
    return {
        "script_loaded": state.script_loaded,
        "title": state.metadata.get("title", ""),
        "cameras_connected": list(state.operator_connections.keys()),
        "cues_total": len(state.all_cues),
        "cues_auto": len(auto),
        "cues_manual": len(manual),
        "cues_fired": state.cues_fired_count,
        "engine_pointer": state.engine.pointer if state.engine else 0,
        "uptime_seconds": int(time.time() - state.start_time),
        "stt_active": state.stt_active,
        "stt_device": state.stt_device,
        "stt_engine": state.stt_engine,
        "queues_pending": {
            cam_id: q.qsize()
            for cam_id, q in state.camera_queues.items()
        },
    }

@app.get("/api/cues")
async def api_cues():
    if not state.script_loaded:
        raise HTTPException(status_code=400, detail="Nessun copione caricato")
    return [
        {
            "cue_id": c.cue_id,
            "act_id": c.act_id,
            "scene_id": c.scene_id,
            "type": c.trigger.type,
            "trigger_text": c.trigger.text,
            "cameras": [i.camera for i in c.instructions],
            "fired": c.fired,
        }
        for c in state.all_cues
    ]

@app.get("/api/script/lines")
async def api_script_lines():
    """Script lines con cue risolti inline — per il prompter."""
    if not state.script_loaded:
        raise HTTPException(status_code=400, detail="Nessun copione caricato")
    cue_index = {c.cue_id: c for c in state.all_cues}
    result = []
    for block in state.script_lines:
        if block.get("type") == "cue_ref":
            cue_id = block.get("cue_id", "")
            cue = cue_index.get(cue_id)
            if cue:
                result.append({
                    "type": "cue_ref",
                    "cue_id": cue_id,
                    "cue_type": cue.trigger.type,
                    "trigger_text": cue.trigger.text,
                    "cameras": [i.camera for i in cue.instructions],
                    "instructions": [{"camera": i.camera, "text": i.text} for i in cue.instructions],
                    "fired": cue.fired,
                })
        else:
            result.append(block)
    return result

class FireCueRequest(BaseModel):
    cue_id: str

@app.post("/api/cues/fire")
async def api_fire_cue(req: FireCueRequest):
    log.info(f"🎯 Fire cue manuale: {req.cue_id}")
    if not state.engine:
        log.warning(f"  Fire {req.cue_id} rifiutato — engine non inizializzato")
        raise HTTPException(status_code=400, detail="Engine non inizializzato")
    target = next((c for c in state.all_cues if c.cue_id == req.cue_id), None)
    if not target:
        log.warning(f"  Fire {req.cue_id} rifiutato — cue non trovato")
        raise HTTPException(status_code=404, detail=f"Cue {req.cue_id} non trovato")
    if target.fired:
        log.warning(f"  Fire {req.cue_id} rifiutato — già scattato")
        raise HTTPException(status_code=409, detail="Cue già scattato")

    target.fired = True
    fc = FiredCue(cue=target, matched_text="[MANUALE]", confidence=1.0)
    state.cues_fired_count += 1
    if target.trigger.type == "line":
        state.engine.pointer = max(state.engine.pointer, state.all_cues.index(target) + 1)
    await _dispatch_cue(fc)
    return {"ok": True, "cue_id": req.cue_id}

@app.post("/api/cues/skip")
async def api_skip_cue(req: FireCueRequest):
    """Marca un cue come fired senza inviare istruzioni. Avanza il puntatore."""
    if not state.engine:
        raise HTTPException(status_code=400, detail="Engine non inizializzato")
    target = next((c for c in state.all_cues if c.cue_id == req.cue_id), None)
    if not target:
        raise HTTPException(status_code=404, detail=f"Cue {req.cue_id} non trovato")
    if target.fired:
        raise HTTPException(status_code=409, detail="Cue già scattato")
    target.fired = True
    state.cues_fired_count += 1
    if target.trigger.type == "line" and target in state.engine.cues:
        eng_idx = state.engine.cues.index(target)
        state.engine.pointer = max(state.engine.pointer, eng_idx + 1)
    await notify_directors({
        "type": "cue_skipped",
        "cue_id": req.cue_id,
        "cameras": [i.camera for i in target.instructions],
    })
    log.info(f"⏭ CUE SKIP: {req.cue_id}")
    return {"ok": True, "cue_id": req.cue_id, "skipped": True}

@app.post("/api/engine/reset")
async def api_reset():
    log.info("🔄 Engine RESET richiesto")
    if state.engine:
        old_pointer = state.engine.pointer
        old_fired = state.cues_fired_count
        state.engine.reset()
        for c in state.all_cues:
            c.fired = False
        state.cues_fired_count = 0
        state.last_text.clear()
        state.last_audio.clear()
        # Svuota code camera
        flushed = 0
        for cam_id, q in state.camera_queues.items():
            while not q.empty():
                try:
                    q.get_nowait()
                    flushed += 1
                except asyncio.QueueEmpty:
                    break
        log.info(f"  Reset completato | pointer {old_pointer}→0 | "
                 f"fired {old_fired}→0 | code svuotate: {flushed} item")
    else:
        log.warning("  Reset richiesto ma engine non inizializzato")
    await notify_directors({"type": "engine_reset"})
    return {"ok": True}

class GotoSceneRequest(BaseModel):
    scene_id: int

@app.post("/api/engine/goto-scene")
async def api_goto_scene(req: GotoSceneRequest):
    """Sposta il puntatore dell'engine all'inizio della scena specificata."""
    if not state.engine or not state.script_loaded:
        raise HTTPException(status_code=400, detail="Engine non inizializzato")
    target_idx = next(
        (i for i, c in enumerate(state.all_cues)
         if getattr(c, "scene_id", None) == req.scene_id
         and c.trigger.type == "line"),
        None
    )
    if target_idx is None:
        log.info(f"Goto scena {req.scene_id}: nessun cue auto, puntatore invariato")
    else:
        state.engine.pointer = target_idx
        log.info(f"Goto scena {req.scene_id}: puntatore → {target_idx}")
    await notify_directors({"type": "scene_changed", "scene_id": req.scene_id})
    return {"ok": True, "scene_id": req.scene_id, "pointer": state.engine.pointer if state.engine else 0}

@app.delete("/api/script/clear")
async def api_script_clear():
    """Cancella script.json e svuota cache TTS su disco."""
    deleted = []
    script_path = Path(__file__).parent.parent / "script-parser" / "script.json"
    if script_path.exists():
        script_path.unlink()
        deleted.append("script.json")
        log.info("script.json eliminato")
    tts_cache_dir = Path(__file__).parent.parent / "script-parser" / "audio" / "_tts_cache"
    deleted_audio = 0
    if tts_cache_dir.exists():
        for f in tts_cache_dir.glob("*.mp3"):
            f.unlink()
            deleted_audio += 1
        deleted.append(f"{deleted_audio} file audio TTS")
        log.info(f"Cache TTS svuotata: {deleted_audio} file eliminati")
    if state.tts:
        state.tts._cache.clear()
    state.script_loaded = False
    state.metadata = {}
    state.all_cues = []
    state.script_lines = []
    state.engine = None
    state.cues_fired_count = 0
    state.last_text.clear()
    state.last_audio.clear()
    await notify_directors({"type": "script_cleared"})
    return {"ok": True, "deleted": deleted}

@app.get("/api/tts/test-audio")
async def api_tts_test(text: str = None):
    """Genera audio di test. Se text è specificato usa quello, altrimenti usa il messaggio default."""
    if not state.tts:
        raise HTTPException(status_code=400, detail="TTS non configurato")
    test_text = text or "Audio intercom attivo. Se senti questo messaggio, le cuffie funzionano correttamente. Regola il volume a tuo piacimento."
    loop = asyncio.get_event_loop()
    audio = await loop.run_in_executor(None, state.tts.get_audio, test_text)
    if not audio:
        raise HTTPException(status_code=500, detail="Errore generazione audio di test")
    from fastapi.responses import Response
    return Response(content=audio, media_type="audio/mpeg")

@app.get("/api/audio/devices")
async def api_audio_devices():
    """Lista le periferiche audio di input disponibili sul server."""
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        default_input = sd.default.device[0]
        inputs = []
        for i, dev in enumerate(devices):
            if dev["max_input_channels"] > 0:
                inputs.append({
                    "index": i,
                    "name": dev["name"],
                    "channels": dev["max_input_channels"],
                    "sample_rate": int(dev["default_samplerate"]),
                    "is_default": i == default_input,
                })
        return {"devices": inputs}
    except ImportError:
        return {"devices": [], "note": "sounddevice non disponibile sul server"}
    except Exception as e:
        return {"devices": [], "note": str(e)}

@app.post("/api/tts/pregenerate")
async def api_tts_pregenerate():
    """Rigenera tutti gli audio TTS per il copione corrente."""
    if not state.tts:
        raise HTTPException(status_code=400, detail="TTS non configurato")
    if not state.script_loaded or not state.all_cues:
        raise HTTPException(status_code=400, detail="Nessun copione caricato")
    log.info("TTS pregenerate richiesto dalla dashboard")
    loop = asyncio.get_event_loop()
    stats = await loop.run_in_executor(None, state.tts.pregenerate_all, state.all_cues)
    log.info(f"TTS pregenerate completato: {stats}")
    return {"ok": True, **stats}

class ConvertScriptRequest(BaseModel):
    text: str
    title: str = ""
    date: str = ""
    location: str = ""

@app.post("/api/script/convert")
async def api_script_convert(req: ConvertScriptRequest):
    """Converte testo copione in JSON e lo carica nel server."""
    log.info(f"📝 Conversione copione: title='{req.title}' "
             f"({len(req.text)} chars)")
    try:
        sys.path.append(str(Path(__file__).parent.parent / "tools"))
        from doc_to_script import parse_script
        script = parse_script(req.text, req.title, req.date, req.location)

        # Nome file dal titolo
        title = script.get("metadata", {}).get("title", "copione")
        safe_name = re.sub(r'[^\w\s-]', '', title).strip()
        safe_name = re.sub(r'[\s-]+', '_', safe_name).lower() or "copione"
        script_dir = Path(__file__).parent.parent / "script-parser"
        script_path = script_dir / f"{safe_name}.json"

        script_path.write_text(
            json.dumps(script, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        log.info(f"  Copione salvato: {script_path}")

        # Carica nel server
        meta, cues = await load_script_file(str(script_path))
        auto = get_auto_cues(cues)
        manual = get_manual_cues(cues)

        await notify_directors({"type": "script_loaded", "metadata": meta})
        return {
            "ok": True,
            "title": meta.get("title", ""),
            "cues_total": len(cues),
            "cues_auto": len(auto),
            "cues_manual": len(manual),
        }
    except Exception as e:
        log.error(f"  Conversione fallita: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/tts/voices")
async def api_tts_voices():
    """Lista le voci disponibili su ElevenLabs."""
    if not state.tts:
        raise HTTPException(status_code=400, detail="TTS non configurato")
    voices = state.tts.list_voices()
    return {"voices": voices}

class TTSConfigRequest(BaseModel):
    voice_id: Optional[str] = None
    stability: Optional[float] = None
    similarity_boost: Optional[float] = None
    style: Optional[float] = None
    use_speaker_boost: Optional[bool] = None

@app.post("/api/tts/config")
async def api_tts_config(req: TTSConfigRequest):
    """Applica la configurazione TTS al motore in esecuzione."""
    if not state.tts:
        raise HTTPException(status_code=400, detail="TTS non configurato")
    if req.voice_id:
        state.tts.voice_id = req.voice_id
        state.tts._cache.clear()
        log.info(f"TTS voce aggiornata: {req.voice_id}")
    if req.stability is not None:
        state.tts._settings_override = getattr(state.tts, '_settings_override', {})
        state.tts._settings_override['stability'] = req.stability
    if req.similarity_boost is not None:
        state.tts._settings_override = getattr(state.tts, '_settings_override', {})
        state.tts._settings_override['similarity_boost'] = req.similarity_boost
    if req.style is not None:
        state.tts._settings_override = getattr(state.tts, '_settings_override', {})
        state.tts._settings_override['style'] = req.style
    if req.use_speaker_boost is not None:
        state.tts._settings_override = getattr(state.tts, '_settings_override', {})
        state.tts._settings_override['use_speaker_boost'] = req.use_speaker_boost
    if hasattr(state.tts, '_settings_override') and state.tts._settings_override:
        from tts_engine import TTS_SETTINGS
        for k, v in state.tts._settings_override.items():
            TTS_SETTINGS[k] = v
    return {"ok": True, "voice_id": state.tts.voice_id}

class RegenerateRequest(BaseModel):
    text: str

@app.post("/api/tts/regenerate")
async def api_tts_regenerate(req: RegenerateRequest):
    """
    Rigenera l'audio TTS per un testo specifico.
    Usato quando un'istruzione viene modificata durante l'evento.
    """
    if not state.tts:
        raise HTTPException(status_code=400, detail="TTS non configurato")
    log.info(f"🔄 TTS regenerate: '{req.text[:50]}'")
    t0 = time.time()
    loop = asyncio.get_event_loop()
    audio = await loop.run_in_executor(None, state.tts.regenerate, req.text)
    elapsed = (time.time() - t0) * 1000
    if not audio:
        log.error(f"  TTS regenerate fallito dopo {elapsed:.0f}ms")
        raise HTTPException(status_code=500, detail="Errore generazione TTS")
    log.info(f"  TTS regenerato: {len(audio)}B in {elapsed:.0f}ms")
    return {"ok": True, "bytes": len(audio)}

class STTStartRequest(BaseModel):
    device: int = 7
    engine: str = "deepgram"

@app.get("/api/stt/token")
async def api_stt_token():
    """Restituisce la API key Deepgram per lo STT nel browser."""
    key = os.environ.get("DEEPGRAM_API_KEY", "")
    if not key:
        raise HTTPException(status_code=503, detail="DEEPGRAM_API_KEY non configurata sul server")
    return {"api_key": key}

@app.post("/api/stt/start")
async def api_stt_start(req: STTStartRequest):
    """Registra che lo STT è stato avviato — notifica la dashboard."""
    state.stt_active = True
    state.stt_device = req.device
    state.stt_engine = req.engine
    await notify_directors({
        "type": "stt_started",
        "device": req.device,
        "engine": req.engine,
    })
    log.info(f"STT avviato — device {req.device}, engine {req.engine}")
    return {"ok": True}

@app.post("/api/stt/stop")
async def api_stt_stop(source: str = ""):
    """Segnala che lo STT deve fermarsi.
    Se source='browser', non notifica gli altri client per non fermare il CLI."""
    state.stt_active = False
    if source != "browser":
        await notify_directors({"type": "stt_stopped"})
    log.info(f"STT fermato (source={source or 'dashboard'})")
    return {"ok": True}

class STTChunkRequest(BaseModel):
    text: str

@app.post("/api/stt/chunk")
async def api_stt_chunk(req: STTChunkRequest):
    try:
        if not state.engine:
            return {"fired": [], "pointer": 0}
        log.debug(f"🎤 STT chunk (ptr={state.engine.pointer}): '{req.text[:80]}'")
        fired = state.engine.process(req.text)
        # Notifica la regia con la trascrizione — aggiorna box STT nel prompter
        await notify_directors({"type": "stt_transcript", "text": req.text})
        if fired:
            log.info(f"🎤 STT → {len(fired)} cue fired: "
                     f"{[f.cue.cue_id for f in fired]} | ptr={state.engine.pointer}")
        return {
            "fired": [f.cue.cue_id for f in fired],
            "pointer": state.engine.pointer,
        }
    except Exception as e:
        log.error(f"Errore stt/chunk: {e}", exc_info=True)
        return {"fired": [], "pointer": 0, "error": str(e)}

# ---------------------------------------------------------------------------
# API Logging — lettura log e controllo livello
# ---------------------------------------------------------------------------
@app.get("/api/logs/tail")
async def api_logs_tail(lines: int = 100):
    """Ultime N righe del log — per la dashboard."""
    log_file = LOG_DIR / "intercom.log"
    if not log_file.exists():
        return {"lines": [], "file": str(log_file)}
    try:
        text = log_file.read_text(encoding="utf-8")
        all_lines = text.strip().split("\n")
        return {"lines": all_lines[-lines:], "total": len(all_lines)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/logs/level")
async def api_logs_level(level: str = "INFO"):
    """Cambia il log level a runtime — utile per debug temporaneo."""
    level = level.upper()
    if level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
        raise HTTPException(status_code=400, detail=f"Livello non valido: {level}")
    logging.getLogger().setLevel(getattr(logging, level))
    log.info(f"📝 Log level cambiato a {level}")
    return {"ok": True, "level": level}

# ---------------------------------------------------------------------------
# WebSocket — Operatori Camera
# ---------------------------------------------------------------------------
@app.websocket("/ws/camera/{camera_id}")
async def ws_camera(websocket: WebSocket, camera_id: int):
    await websocket.accept()
    old_ws = state.operator_connections.get(camera_id)
    if old_ws:
        log.warning(f"📷 CAM{camera_id} riconnessa — sostituisco vecchia connessione")
    state.operator_connections[camera_id] = websocket
    state.camera_last_ping[camera_id] = time.time()
    _start_camera_worker(camera_id)
    log.info(f"📷 CAM{camera_id} connessa (+ queue worker) | "
             f"camere online: {list(state.operator_connections.keys())}")

    await notify_directors({
        "type": "camera_connected",
        "camera": camera_id,
        "cameras_online": list(state.operator_connections.keys()),
    })

    # Se c'è una conference attiva che include questa camera, invitala
    if conference_state["active"] and camera_id in conference_state["cameras"]:
        try:
            conf_room = conference_state["room"]
            token = generate_token(
                identity=f"cam{camera_id}",
                room=conf_room,
                can_publish=True,
                can_subscribe=True,
            )
            await websocket.send_text(json.dumps({
                "type": "join_conference",
                "token": token,
                "room": conf_room,
            }))
            log.info(f"  CAM{camera_id} auto-join conference room '{conf_room}'")
        except Exception as e:
            log.error(f"  CAM{camera_id} auto-join conference fallito: {e}")

    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)

            if msg.get("type") == "ping":
                state.camera_last_ping[camera_id] = time.time()
                await websocket.send_text(json.dumps({"type": "pong"}))

            elif msg.get("type") == "replay":
                audio = state.last_audio.get(camera_id)
                last = state.last_text.get(camera_id)
                if audio:
                    log.info(f"  CAM{camera_id} replay audio ({len(audio)}B)")
                    header = json.dumps({
                        "type": "replay",
                        "camera": camera_id,
                        "text": last.get("text", "") if last else "",
                    })
                    await websocket.send_text(header)
                    await websocket.send_bytes(audio)
                elif last:
                    log.info(f"  CAM{camera_id} replay testo")
                    await websocket.send_text(json.dumps({
                        "type": "instruction_text",
                        "cue_id": last.get("cue_id", ""),
                        "camera": camera_id,
                        "text": last.get("text", ""),
                    }))
                else:
                    log.debug(f"  CAM{camera_id} replay — nulla da riprodurre")
                    await websocket.send_text(json.dumps({"type": "no_replay"}))

            else:
                log.debug(f"  CAM{camera_id} msg sconosciuto: {msg.get('type')}")

    except WebSocketDisconnect:
        state.operator_connections.pop(camera_id, None)
        state.camera_last_ping.pop(camera_id, None)
        _stop_camera_worker(camera_id)
        log.info(f"📷 CAM{camera_id} disconnessa (queue worker fermato) | "
                 f"camere online: {list(state.operator_connections.keys())}")
        await notify_directors({
            "type": "camera_disconnected",
            "camera": camera_id,
            "cameras_online": list(state.operator_connections.keys()),
        })
    except Exception as e:
        state.operator_connections.pop(camera_id, None)
        state.camera_last_ping.pop(camera_id, None)
        _stop_camera_worker(camera_id)
        log.error(f"📷 CAM{camera_id} errore WS (queue worker fermato): {e}",
                  exc_info=True)
        await notify_directors({
            "type": "camera_disconnected",
            "camera": camera_id,
            "cameras_online": list(state.operator_connections.keys()),
        })

# ---------------------------------------------------------------------------
# WebSocket — Pannello Regia
# ---------------------------------------------------------------------------
@app.websocket("/ws/director")
async def ws_director(websocket: WebSocket):
    await websocket.accept()
    state.director_connections.append(websocket)
    log.info(f"🎬 Pannello regia connesso | "
             f"totale connessioni regia: {len(state.director_connections)}")

    await websocket.send_text(json.dumps({
        "type": "init",
        "cameras_online": list(state.operator_connections.keys()),
        "cues_fired": state.cues_fired_count,
        "script_loaded": state.script_loaded,
        "title": state.metadata.get("title", ""),
    }))

    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            if msg.get("type") == "fire_cue":
                log.debug(f"  Regia WS → fire_cue: {msg.get('cue_id')}")
                await api_fire_cue(FireCueRequest(cue_id=msg["cue_id"]))
            elif msg.get("type") == "reset":
                log.debug("  Regia WS → reset")
                await api_reset()
            else:
                log.debug(f"  Regia WS msg: {msg.get('type')}")

    except WebSocketDisconnect:
        if websocket in state.director_connections:
            state.director_connections.remove(websocket)
        log.info(f"🎬 Pannello regia disconnesso | "
                 f"restano: {len(state.director_connections)}")
    except Exception as e:
        if websocket in state.director_connections:
            state.director_connections.remove(websocket)
        log.error(f"🎬 Errore WS regia: {e}", exc_info=True)

# ---------------------------------------------------------------------------
# Manifest dinamico per camera — permette installazione PWA con cam corretta
# ---------------------------------------------------------------------------
from fastapi.responses import JSONResponse

@app.get("/operator/manifest.json")
async def dynamic_manifest(cam: int = 1):
    """
    Serve il manifest PWA con start_url personalizzato per ogni camera.
    Esempio: /operator/manifest.json?cam=2
    """
    return JSONResponse(content={
        "name": f"TV Intercom — CAM {cam}",
        "short_name": f"CAM {cam}",
        "description": f"App operatore Camera {cam} — TV Intercom",
        "start_url": f"/operator/?cam={cam}",
        "display": "standalone",
        "background_color": "#0a0a0a",
        "theme_color": "#0a0a0a",
        "orientation": "portrait",
        "prefer_related_applications": False,
        "icons": [
            {"src": "/operator/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": "/operator/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ]
    })

# ---------------------------------------------------------------------------
# LiveKit — Comunicazioni vocali
# ---------------------------------------------------------------------------
from livekit_manager import (
    generate_operator_token,
    generate_all_director_tokens,
    generate_token,
    get_livekit_info,
    LIVEKIT_URL,
    NUM_CAMERAS,
    room_name,
    ROOM_GENERAL,
)

# Conference room state
conference_state = {
    "active": False,
    "cameras": [],
    "room": "intercom-conference",
}

@app.get("/api/livekit/info")
async def api_livekit_info():
    """Info configurazione LiveKit."""
    return get_livekit_info()

@app.get("/api/livekit/token/operator/{cam_id}")
async def api_operator_token(cam_id: int):
    """Genera token per operatore camera N."""
    if cam_id < 1 or cam_id > NUM_CAMERAS:
        raise HTTPException(status_code=400, detail=f"Camera {cam_id} non valida")
    token = generate_operator_token(cam_id)
    log.info(f"🔑 Token operatore generato per CAM{cam_id}")
    return {
        "token": token,
        "url": LIVEKIT_URL,
        "room": room_name(cam_id),
        "identity": f"cam{cam_id}",
    }

@app.get("/api/livekit/token/director")
async def api_director_tokens():
    """Genera tutti i token per il pannello regia."""
    tokens = generate_all_director_tokens()
    log.info(f"🔑 Token regia generati ({len(tokens)} room)")
    return {
        "tokens": tokens,
        "url": LIVEKIT_URL,
        "rooms": {
            "general": ROOM_GENERAL,
            **{f"cam{i}": room_name(i) for i in range(1, NUM_CAMERAS + 1)}
        }
    }


# ---------------------------------------------------------------------------
# LiveKit — Conference Room
# ---------------------------------------------------------------------------
class ConferenceRequest(BaseModel):
    cameras: list[int]

@app.post("/api/livekit/conference/open")
async def api_conference_open(req: ConferenceRequest):
    """Apre una conference room e genera token per camere e regia."""
    conf_room = conference_state["room"]
    conference_state["active"] = True
    conference_state["cameras"] = req.cameras

    director_token = generate_token(
        identity="director-conf",
        room=conf_room,
        can_publish=False,    # Regia solo in ascolto nella conference
        can_subscribe=True,
    )

    camera_tokens = []
    for cam_id in req.cameras:
        token = generate_token(
            identity=f"cam{cam_id}",
            room=conf_room,
            can_publish=True,
            can_subscribe=True,
        )
        camera_tokens.append({"cam_id": cam_id, "token": token})

    # Notifica le camere via WebSocket di unirsi alla conference
    for cam_id in req.cameras:
        ws = state.operator_connections.get(cam_id)
        if ws:
            try:
                token_data = next(t for t in camera_tokens if t["cam_id"] == cam_id)
                await ws.send_text(json.dumps({
                    "type": "join_conference",
                    "token": token_data["token"],
                    "room": conf_room,
                }))
                log.info(f"Conference: notificata CAM {cam_id}")
            except Exception as e:
                log.error(f"Conference: errore notifica CAM {cam_id}: {e}")

    log.info(f"Conference aperta: CAM {req.cameras}")
    return {
        "ok": True,
        "url": LIVEKIT_URL,
        "director_token": director_token,
        "camera_tokens": camera_tokens,
        "room": conf_room,
    }

@app.post("/api/livekit/conference/close")
async def api_conference_close():
    """Chiude la conference room — notifica le camere di uscire."""
    cameras = conference_state.get("cameras", [])
    for cam_id in cameras:
        ws = state.operator_connections.get(cam_id)
        if ws:
            try:
                await ws.send_text(json.dumps({"type": "leave_conference"}))
            except Exception:
                pass
    conference_state["active"] = False
    conference_state["cameras"] = []
    log.info("Conference chiusa")
    return {"ok": True}

# ---------------------------------------------------------------------------
# Serve PWA operatore
# ---------------------------------------------------------------------------
client_path = Path(__file__).parent.parent / "client-operator"
if client_path.exists():
    app.mount("/operator", StaticFiles(directory=str(client_path), html=True), name="operator")

# Serve pannello regia LiveKit
regia_path = Path(__file__).parent.parent / "client-regia"
if regia_path.exists():
    app.mount("/regia", StaticFiles(directory=str(regia_path), html=True), name="regia")

# ---------------------------------------------------------------------------
# Serve dashboard control room
# ---------------------------------------------------------------------------
from fastapi.responses import FileResponse

@app.get("/")
async def dashboard():
    """Serve la Control Room dashboard."""
    dashboard_path = Path(__file__).parent.parent / "tools" / "dashboard.html"
    if dashboard_path.exists():
        return FileResponse(str(dashboard_path))
    return {"message": "TV Intercom Server running. Dashboard non trovata in tools/dashboard.html"}
