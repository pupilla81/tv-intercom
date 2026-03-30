"""
server/livekit_manager.py
-------------------------
Gestisce le room LiveKit per il layer comunicazioni vocali.
Architettura: una room per camera + room general.

Room:
    "general"  — regia sempre dentro in ascolto
    "cam1"     — CAM1 + regia quando attiva quel canale
    "cam2"     — CAM2 + regia quando attiva quel canale
    ...

Token generati per:
    - Regia: accesso a tutte le room
    - Operatore N: accesso solo a room "camN"
"""

import os
import time
import logging
from typing import Optional
from livekit.api import AccessToken, VideoGrants
from datetime import timedelta


log = logging.getLogger("intercom.livekit")

# ---------------------------------------------------------------------------
# Configurazione
# ---------------------------------------------------------------------------
LIVEKIT_URL    = os.environ.get("LIVEKIT_URL", "ws://localhost:7880")
LIVEKIT_KEY    = os.environ.get("LIVEKIT_KEY", "APImUBHGnXDL4MP")
LIVEKIT_SECRET = os.environ.get("LIVEKIT_SECRET", "DvY79pRJ47gOTBKZDtmLCH3NgZ4WFGhyqTTs4mgcdNL")

NUM_CAMERAS = 5

ROOM_GENERAL = "intercom-general"

log.info(f"LiveKit config: url={LIVEKIT_URL} cameras={NUM_CAMERAS}")

def room_name(cam_id: int) -> str:
    return f"intercom-cam{cam_id}"

# ---------------------------------------------------------------------------
# Generazione token
# ---------------------------------------------------------------------------
def generate_token(
    identity: str,
    room: str,
    can_publish: bool = True,
    can_subscribe: bool = True,
    ttl_seconds: int = 14400,
) -> str:
    log.debug(f"Token: identity={identity} room={room} "
              f"pub={can_publish} sub={can_subscribe} ttl={ttl_seconds}s")
    token = (
        AccessToken(LIVEKIT_KEY, LIVEKIT_SECRET)
        .with_identity(identity)
        .with_name(identity)
        .with_ttl(timedelta(seconds=ttl_seconds))
        .with_grants(VideoGrants(
            room_join=True,
            room=room,
            room_create=True,
            can_publish=can_publish,
            can_subscribe=can_subscribe,
            can_publish_data=True,
        ))
    )
    return token.to_jwt()


def generate_operator_token(cam_id: int) -> str:
    """Token per operatore camera N — accede solo alla sua room."""
    return generate_token(
        identity=f"cam{cam_id}",
        room=room_name(cam_id),
        can_publish=True,   # può parlare (PTT)
        can_subscribe=True, # sente la regia
    )


def generate_director_token(room: str, can_publish: bool = True) -> str:
    """Token per la regia — accede a una room specifica."""
    return generate_token(
        identity="director",
        room=room,
        can_publish=can_publish,
        can_subscribe=True,
    )


def generate_all_director_tokens() -> dict:
    """
    Genera token per tutte le room — usato all'avvio del pannello regia.
    La regia riceve un token per ogni room e li usa per entrare/uscire.
    """
    tokens = {}
    # Token per room general (ascolto passivo di tutti)
    tokens["general"] = generate_director_token(ROOM_GENERAL, can_publish=False)
    # Token per ogni camera
    for i in range(1, NUM_CAMERAS + 1):
        tokens[f"cam{i}"] = generate_director_token(room_name(i), can_publish=True)
    return tokens


# ---------------------------------------------------------------------------
# Info sistema
# ---------------------------------------------------------------------------
def get_livekit_info() -> dict:
    return {
        "url": LIVEKIT_URL,
        "rooms": [ROOM_GENERAL] + [room_name(i) for i in range(1, NUM_CAMERAS + 1)],
        "num_cameras": NUM_CAMERAS,
    }
