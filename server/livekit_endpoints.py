# ---------------------------------------------------------------------------
# Endpoints da aggiungere a server/main.py
# Incollare PRIMA del mount StaticFiles
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# LiveKit — Comunicazioni vocali
# ---------------------------------------------------------------------------
from livekit_manager import (
    generate_operator_token,
    generate_all_director_tokens,
    get_livekit_info,
    LIVEKIT_URL,
    NUM_CAMERAS,
    room_name,
    ROOM_GENERAL,
)

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
