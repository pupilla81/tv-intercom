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
import sys
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
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
    allow_origins=["*"],
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

state = AppState()

# ---------------------------------------------------------------------------
# Caricamento copione all'avvio
# ---------------------------------------------------------------------------
SCRIPT_PATH = Path(__file__).parent.parent / "script-parser" / "sample_script.json"

@app.on_event("startup")
async def startup():
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

# ---------------------------------------------------------------------------
# Helper: notifica la regia
# ---------------------------------------------------------------------------
async def notify_directors(event: dict):
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
# Helper: recupera audio
# ---------------------------------------------------------------------------
async def _get_audio(instr) -> Optional[bytes]:
    # 1. File pre-registrato manuale (priorità massima)
    if instr.audio_file:
        p = Path(__file__).parent.parent / "script-parser" / instr.audio_file
        if p.exists():
            return p.read_bytes()
        log.warning(f"File audio non trovato: {instr.audio_file}")

    # 2. TTS ElevenLabs (dalla cache o generato al volo)
    if state.tts and instr.text:
        loop = asyncio.get_event_loop()
        audio = await loop.run_in_executor(None, state.tts.get_audio, instr.text)
        if audio:
            return audio

    # 3. Nessun audio disponibile — fallback testo
    log.info(f"  [no audio] testo: '{instr.text}'")
    return None

# ---------------------------------------------------------------------------
# Helper: invia istruzione a una camera (audio o testo come fallback)
# ---------------------------------------------------------------------------
async def send_instruction_to_camera(instr, cue_id: str):
    ws = state.operator_connections.get(instr.camera)
    if not ws:
        log.warning(f"Camera {instr.camera} non connessa — cue {cue_id} non recapitato")
        return

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
            log.info(f"  → CAM {instr.camera}: audio inviato ({len(audio)} bytes)")
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
            log.info(f"  → CAM {instr.camera}: testo inviato (no audio)")
    except Exception as e:
        log.error(f"Errore invio CAM {instr.camera}: {e}")
        state.operator_connections.pop(instr.camera, None)

# ---------------------------------------------------------------------------
# Callback CueEngine → Dispatcher
# ---------------------------------------------------------------------------
def on_cue_fired(fc: FiredCue):
    state.cues_fired_count += 1
    log.info(f"🔔 CUE: {fc.cue.cue_id} (conf: {fc.confidence:.0%})")
    asyncio.create_task(_dispatch_cue(fc))

async def _dispatch_cue(fc: FiredCue):
    tasks = [send_instruction_to_camera(instr, fc.cue.cue_id) for instr in fc.cue.instructions]
    if tasks:
        await asyncio.gather(*tasks)
    await notify_directors({
        "type": "cue_fired",
        "cue_id": fc.cue.cue_id,
        "confidence": round(fc.confidence, 2),
        "cameras": [i.camera for i in fc.cue.instructions],
        "matched_text": fc.matched_text,
    })

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

    # Pre-genera tutti gli audio TTS in background
    if state.tts:
        log.info("TTS pre-generazione audio in corso...")
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, state.tts.pregenerate_all, cues)

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

class FireCueRequest(BaseModel):
    cue_id: str

@app.post("/api/cues/fire")
async def api_fire_cue(req: FireCueRequest):
    if not state.engine:
        raise HTTPException(status_code=400, detail="Engine non inizializzato")
    target = next((c for c in state.all_cues if c.cue_id == req.cue_id), None)
    if not target:
        raise HTTPException(status_code=404, detail=f"Cue {req.cue_id} non trovato")
    if target.fired:
        raise HTTPException(status_code=409, detail="Cue già scattato")

    target.fired = True
    fc = FiredCue(cue=target, matched_text="[MANUALE]", confidence=1.0)
    state.cues_fired_count += 1
    if target.trigger.type == "line":
        state.engine.pointer = max(state.engine.pointer, state.all_cues.index(target) + 1)
    await _dispatch_cue(fc)
    return {"ok": True, "cue_id": req.cue_id}

@app.post("/api/engine/reset")
async def api_reset():
    if state.engine:
        state.engine.reset()
        for c in state.all_cues:
            c.fired = False
        state.cues_fired_count = 0
        state.last_text.clear()
        state.last_audio.clear()
    await notify_directors({"type": "engine_reset"})
    return {"ok": True}

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
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class ConvertScriptRequest(BaseModel):
    text: str
    title: str = ""
    date: str = ""
    location: str = ""

@app.post("/api/script/convert")
async def api_script_convert(req: ConvertScriptRequest):
    """Converte testo copione in JSON e lo carica nel server."""
    try:
        sys.path.append(str(Path(__file__).parent.parent / "tools"))
        from doc_to_script import parse_script
        script = parse_script(req.text, req.title, req.date, req.location)

        # Salva il file
        script_path = Path(__file__).parent.parent / "script-parser" / "script.json"
        script_path.write_text(
            json.dumps(script, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

        # Carica nel server
        meta, cues = await load_script_file(str(script_path))
        auto = get_auto_cues(cues)
        manual = get_manual_cues(cues)

        await notify_directors({"type": "script_loaded", "metadata": meta})
        return {
            "ok": True,
            "title": meta["title"],
            "cues_total": len(cues),
            "cues_auto": len(auto),
            "cues_manual": len(manual),
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/tts/voices")
async def api_tts_voices():
    """Lista le voci disponibili su ElevenLabs."""
    if not state.tts:
        raise HTTPException(status_code=400, detail="TTS non configurato")
    voices = state.tts.list_voices()
    return {"voices": voices}

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
    loop = asyncio.get_event_loop()
    audio = await loop.run_in_executor(None, state.tts.regenerate, req.text)
    if not audio:
        raise HTTPException(status_code=500, detail="Errore generazione TTS")
    return {"ok": True, "bytes": len(audio)}

class STTStartRequest(BaseModel):
    device: int = 7
    engine: str = "deepgram"

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
async def api_stt_stop():
    """Segnala che lo STT deve fermarsi — notifica la dashboard."""
    state.stt_active = False
    await notify_directors({"type": "stt_stopped"})
    log.info("STT fermato dalla dashboard")
    return {"ok": True}

class STTChunkRequest(BaseModel):
    text: str

@app.post("/api/stt/chunk")
async def api_stt_chunk(req: STTChunkRequest):
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

            if msg.get("type") == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))

            elif msg.get("type") == "replay":
                audio = state.last_audio.get(camera_id)
                last = state.last_text.get(camera_id)
                if audio:
                    header = json.dumps({
                        "type": "replay",
                        "camera": camera_id,
                        "text": last.get("text", "") if last else "",
                    })
                    await websocket.send_text(header)
                    await websocket.send_bytes(audio)
                elif last:
                    # Replay solo testo
                    await websocket.send_text(json.dumps({
                        "type": "instruction_text",
                        "cue_id": last.get("cue_id", ""),
                        "camera": camera_id,
                        "text": last.get("text", ""),
                    }))
                else:
                    await websocket.send_text(json.dumps({"type": "no_replay"}))

    except WebSocketDisconnect:
        state.operator_connections.pop(camera_id, None)
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
                await api_fire_cue(FireCueRequest(cue_id=msg["cue_id"]))
            elif msg.get("type") == "reset":
                await api_reset()

    except WebSocketDisconnect:
        state.director_connections.remove(websocket)
        log.info("🎬 Pannello regia disconnesso")

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
        can_publish=True,
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

@app.get("/api/scripts")
async def api_list_scripts():
    """Lista i file JSON nella cartella script-parser — per selezione da dashboard."""
    script_dir = Path(__file__).parent.parent / "script-parser"
    files = sorted(script_dir.glob("*.json"))
    return {"files": [f.name for f in files]}


@app.get("/api/script/download")
async def api_script_download():
    """Scarica il file JSON del copione correntemente caricato."""
    from fastapi.responses import FileResponse as FR
    script_path = Path(__file__).parent.parent / "script-parser" / "script.json"
    sample_path = Path(__file__).parent.parent / "script-parser" / "sample_script.json"
    target = script_path if script_path.exists() else sample_path
    if not target.exists():
        raise HTTPException(status_code=404, detail="Nessun file copione trovato")
    return FR(str(target), media_type="application/json",
              headers={"Content-Disposition": f"attachment; filename={target.name}"})


class TTSConfigRequest(BaseModel):
    voice_id: Optional[str] = None
    stability: Optional[float] = None
    similarity_boost: Optional[float] = None
    style: Optional[float] = None
    use_speaker_boost: Optional[bool] = None

@app.post("/api/tts/config")
async def api_tts_config(req: TTSConfigRequest):
    """Aggiorna voce e parametri TTS a runtime — effetto immediato sul prossimo audio generato."""
    if not state.tts:
        raise HTTPException(status_code=400, detail="TTS non configurato")
    settings = {}
    if req.stability       is not None: settings["stability"]        = req.stability
    if req.similarity_boost is not None: settings["similarity_boost"] = req.similarity_boost
    if req.style           is not None: settings["style"]            = req.style
    if req.use_speaker_boost is not None: settings["use_speaker_boost"] = req.use_speaker_boost
    state.tts.update_config(
        voice_id=req.voice_id,
        settings=settings if settings else None
    )
    return {"ok": True, "voice_id": state.tts.voice_id, "settings": state.tts.voice_settings}


@app.post("/api/tts/pregenerate")
async def api_tts_pregenerate():
    """Rigenera tutti gli audio TTS per il copione corrente (utile dopo cambio voce/parametri)."""
    if not state.tts:
        raise HTTPException(status_code=400, detail="TTS non configurato")
    if not state.script_loaded:
        raise HTTPException(status_code=400, detail="Nessun copione caricato")
    # Svuota la cache per forzare la rigenerazione
    state.tts._cache.clear()
    loop = asyncio.get_event_loop()
    stats = await loop.run_in_executor(None, state.tts.pregenerate_all, state.all_cues)
    log.info(f"TTS pregenerate: {stats}")
    return {"ok": True, **stats}


class GotoSceneRequest(BaseModel):
    act_id: int
    scene_id: int

@app.post("/api/engine/goto")
async def api_engine_goto(req: GotoSceneRequest):
    """Sposta il puntatore dell'engine all'inizio della scena indicata senza scattare cue."""
    if not state.engine:
        raise HTTPException(status_code=400, detail="Engine non inizializzato")
    # Trova il primo cue della scena
    idx = next(
        (i for i, c in enumerate(state.all_cues)
         if c.act_id == req.act_id and c.scene_id == req.scene_id),
        None
    )
    if idx is None:
        raise HTTPException(status_code=404, detail=f"Scena A{req.act_id}/S{req.scene_id} non trovata")
    state.engine.pointer = idx
    # Reset fired per i cue dalla scena in poi (opzionale ma utile)
    for c in state.all_cues[idx:]:
        c.fired = False
    await notify_directors({"type": "engine_goto", "act_id": req.act_id, "scene_id": req.scene_id, "pointer": idx})
    log.info(f"Engine goto: A{req.act_id}/S{req.scene_id} → ptr={idx}")
    return {"ok": True, "pointer": idx}


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
