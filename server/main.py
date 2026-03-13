"""
server/main.py
--------------
Hub centrale del sistema TV Intercom.
Gestisce connessioni WebSocket degli operatori, stato del copione,
invio istruzioni audio ai canali camera e trigger manuali dalla regia.

Avvio:
    uvicorn main:app --host 0.0.0.0 --port 8000
"""

import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# Aggiungi il path del script-parser
sys.path.append(str(Path(__file__).parent.parent / "script-parser"))
from script_parser import load_script, get_auto_cues, get_manual_cues, Cue
from cue_engine import CueEngine, FiredCue

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("intercom")

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="TV Intercom Server", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In produzione: restringere al dominio della regia
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Stato globale
# ---------------------------------------------------------------------------
class AppState:
    def __init__(self):
        self.script_loaded: bool = False
        self.metadata: dict = {}
        self.all_cues: list[Cue] = []
        self.engine: Optional[CueEngine] = None

        # WebSocket connections
        # camera_id (int) -> WebSocket
        self.operator_connections: dict[int, WebSocket] = {}
        # Lista connessioni pannello regia (possono essere più monitor)
        self.director_connections: list[WebSocket] = []

        # Ultimo messaggio audio per camera (per replay)
        # camera_id -> bytes
        self.last_audio: dict[int, bytes] = {}

        # Statistiche
        self.start_time: float = time.time()
        self.cues_fired_count: int = 0

state = AppState()

# ---------------------------------------------------------------------------
# Caricamento copione all'avvio
# ---------------------------------------------------------------------------
SCRIPT_PATH = Path(__file__).parent.parent / "script-parser" / "sample_script.json"

@app.on_event("startup")
async def startup():
    if SCRIPT_PATH.exists():
        await load_script_file(str(SCRIPT_PATH))
        log.info(f"Copione caricato: {state.metadata.get('title', '?')}")
    else:
        log.warning(f"Nessun copione trovato in {SCRIPT_PATH}")

# ---------------------------------------------------------------------------
# Helper: notifica la regia
# ---------------------------------------------------------------------------
async def notify_directors(event: dict):
    """Invia un evento JSON a tutti i pannelli regia connessi."""
    msg = json.dumps(event)
    dead = []
    for ws in state.director_connections:
        try:
            await ws.send_text(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        state.director_connections.remove(ws)

# ---------------------------------------------------------------------------
# Helper: invia audio a una camera
# ---------------------------------------------------------------------------
async def send_audio_to_camera(camera_id: int, audio_bytes: bytes, cue_id: str):
    """Invia bytes audio (WAV) alla camera specificata via WebSocket."""
    ws = state.operator_connections.get(camera_id)
    if not ws:
        log.warning(f"Camera {camera_id} non connessa — cue {cue_id} non recapitato")
        return

    # Salva per replay
    state.last_audio[camera_id] = audio_bytes

    try:
        # Prima manda un header JSON con metadati
        header = json.dumps({
            "type": "instruction",
            "cue_id": cue_id,
            "camera": camera_id,
        })
        await ws.send_text(header)
        # Poi manda i bytes audio
        await ws.send_bytes(audio_bytes)
        log.info(f"  → CAM {camera_id}: audio inviato ({len(audio_bytes)} bytes)")
    except Exception as e:
        log.error(f"Errore invio CAM {camera_id}: {e}")
        state.operator_connections.pop(camera_id, None)

# ---------------------------------------------------------------------------
# Callback CueEngine → Dispatcher
# ---------------------------------------------------------------------------
def on_cue_fired(fc: FiredCue):
    """
    Chiamata dal CueEngine quando un cue scatta.
    Schedula l'invio audio in parallelo a tutte le camere coinvolte.
    """
    state.cues_fired_count += 1
    log.info(f"🔔 CUE: {fc.cue.cue_id} (conf: {fc.confidence:.0%})")

    asyncio.create_task(_dispatch_cue(fc))

async def _dispatch_cue(fc: FiredCue):
    """Invia le istruzioni audio a tutte le camere del cue, in parallelo."""
    tasks = []
    for instr in fc.cue.instructions:
        audio = await _get_audio(instr)
        if audio:
            tasks.append(send_audio_to_camera(instr.camera, audio, fc.cue.cue_id))

    if tasks:
        await asyncio.gather(*tasks)

    # Notifica pannello regia
    await notify_directors({
        "type": "cue_fired",
        "cue_id": fc.cue.cue_id,
        "confidence": round(fc.confidence, 2),
        "cameras": [i.camera for i in fc.cue.instructions],
        "matched_text": fc.matched_text,
    })

async def _get_audio(instr) -> Optional[bytes]:
    """
    Restituisce i bytes audio per un'istruzione.
    Priorità: file pre-registrato → TTS (da implementare) → None
    """
    if instr.audio_file:
        p = Path(__file__).parent.parent / "script-parser" / instr.audio_file
        if p.exists():
            return p.read_bytes()
        log.warning(f"File audio non trovato: {instr.audio_file}")

    # TODO Modulo TTS: generare audio da instr.text
    # Per ora ritorna None — il client mostrerà il testo come fallback
    log.info(f"  [TTS non ancora implementato] testo: '{instr.text}'")
    return None

# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------

class LoadScriptRequest(BaseModel):
    path: str

async def load_script_file(path: str):
    meta, cues = load_script(path)
    state.metadata = meta
    state.all_cues = cues
    state.script_loaded = True
    auto = get_auto_cues(cues)
    state.engine = CueEngine(auto, on_cue_fired=on_cue_fired)
    return meta, cues

@app.post("/api/script/load")
async def api_load_script(req: LoadScriptRequest):
    try:
        meta, cues = await load_script_file(req.path)
        await notify_directors({"type": "script_loaded", "metadata": meta})
        return {"ok": True, "title": meta["title"], "total_cues": len(cues)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/status")
async def api_status():
    """Stato generale del sistema — usato dal pannello regia al caricamento."""
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
    }

@app.get("/api/cues")
async def api_cues():
    """Lista completa dei cue con stato (fired/pending)."""
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

class FireCueRequest(BaseModel):
    cue_id: str

@app.post("/api/cues/fire")
async def api_fire_cue(req: FireCueRequest):
    """Scatta un cue manualmente dal pannello regia."""
    if not state.engine:
        raise HTTPException(status_code=400, detail="Engine non inizializzato")
    # Cerca il cue in tutti (anche manuali)
    target = next((c for c in state.all_cues if c.cue_id == req.cue_id), None)
    if not target:
        raise HTTPException(status_code=404, detail=f"Cue {req.cue_id} non trovato")

    # Gestisci manuale e automatico
    if target.trigger.type == "manual":
        target.fired = True
        fc = FiredCue(cue=target, matched_text="[MANUALE]", confidence=1.0)
        state.cues_fired_count += 1
        await _dispatch_cue(fc)
    else:
        fc = state.engine.force_fire(req.cue_id)
        if not fc:
            raise HTTPException(status_code=409, detail="Cue già scattato")

    return {"ok": True, "cue_id": req.cue_id}

@app.post("/api/engine/reset")
async def api_reset():
    """Riporta il motore all'inizio (utile per prove e repliche)."""
    if state.engine:
        state.engine.reset()
        for c in state.all_cues:
            c.fired = False
        state.cues_fired_count = 0
    await notify_directors({"type": "engine_reset"})
    return {"ok": True}

class STTChunkRequest(BaseModel):
    text: str

@app.post("/api/stt/chunk")
async def api_stt_chunk(req: STTChunkRequest):
    """
    Riceve un chunk di testo dallo STT e lo passa al CueEngine.
    Chiamato dal Modulo STT in esecuzione sul laptop di regia.
    """
    if not state.engine:
        return {"fired": []}
    fired = state.engine.process(req.text)
    return {
        "fired": [f.cue.cue_id for f in fired],
        "pointer": state.engine.pointer,
    }

# ---------------------------------------------------------------------------
# WebSocket — Operatori Camera
# ---------------------------------------------------------------------------
@app.websocket("/ws/camera/{camera_id}")
async def ws_camera(websocket: WebSocket, camera_id: int):
    await websocket.accept()
    state.operator_connections[camera_id] = websocket
    log.info(f"📷 CAM {camera_id} connessa")

    await notify_directors({
        "type": "camera_connected",
        "camera": camera_id,
        "cameras_online": list(state.operator_connections.keys()),
    })

    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)

            # Richiesta replay ultimo messaggio
            if msg.get("type") == "replay":
                audio = state.last_audio.get(camera_id)
                if audio:
                    header = json.dumps({"type": "replay", "camera": camera_id})
                    await websocket.send_text(header)
                    await websocket.send_bytes(audio)
                    log.info(f"  → CAM {camera_id}: replay inviato")
                else:
                    await websocket.send_text(json.dumps({"type": "no_replay"}))

            # Messaggio testo verso la regia (dal PTT)
            elif msg.get("type") == "ptt_text":
                await notify_directors({
                    "type": "operator_message",
                    "camera": camera_id,
                    "text": msg.get("text", ""),
                })

    except WebSocketDisconnect:
        state.operator_connections.pop(camera_id, None)
        state.last_audio.pop(camera_id, None)
        log.info(f"📷 CAM {camera_id} disconnessa")
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
    log.info("🎬 Pannello regia connesso")

    # Manda subito lo stato corrente
    await websocket.send_text(json.dumps({
        "type": "init",
        "cameras_online": list(state.operator_connections.keys()),
        "cues_fired": state.cues_fired_count,
        "script_loaded": state.script_loaded,
        "title": state.metadata.get("title", ""),
    }))

    try:
        while True:
            # La regia può mandare comandi anche via WebSocket
            data = await websocket.receive_text()
            msg = json.loads(data)

            if msg.get("type") == "fire_cue":
                await api_fire_cue(FireCueRequest(cue_id=msg["cue_id"]))

            elif msg.get("type") == "reset":
                await api_reset()

    except WebSocketDisconnect:
        state.director_connections.remove(websocket)
        log.info("🎬 Pannello regia disconnesso")

# ---------------------------------------------------------------------------
# Serve client files (PWA operatore)
# ---------------------------------------------------------------------------
client_path = Path(__file__).parent.parent / "client-operator"
if client_path.exists():
    app.mount("/operator", StaticFiles(directory=str(client_path), html=True), name="operator")
