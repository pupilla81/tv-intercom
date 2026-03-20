"""
server/tts_engine.py
--------------------
Modulo TTS — genera audio delle istruzioni usando ElevenLabs.
Pre-genera tutti gli audio all'avvio e li tiene in cache.
Rigenera singoli audio se un'istruzione viene modificata durante l'evento.

Voce predefinita: italiana ad alta qualità (Aria o Rachel di ElevenLabs)
"""

import hashlib
import logging
import os
from pathlib import Path
from typing import Optional

import httpx

log = logging.getLogger("intercom.tts")

# ---------------------------------------------------------------------------
# Configurazione
# ---------------------------------------------------------------------------
ELEVENLABS_API_URL = "https://api.elevenlabs.io/v1"

# Voce italiana consigliata — puoi cambiarla dopo aver visto la lista
# Lista voci: https://api.elevenlabs.io/v1/voices
DEFAULT_VOICE_ID = "pNInz6obpgDQGcFmaJgB"  # Adam — chiara e professionale

TTS_MODEL = "eleven_multilingual_v2"  # Supporta italiano nativo

TTS_SETTINGS = {
    "stability": 0.75,          # 0-1: più alto = più uniforme
    "similarity_boost": 0.75,   # 0-1: più alto = più fedele alla voce
    "style": 0.0,
    "use_speaker_boost": True,
}

# Cache locale su disco (evita rigeneration se il server si riavvia)
CACHE_DIR = Path(__file__).parent.parent / "script-parser" / "audio" / "_tts_cache"


# ---------------------------------------------------------------------------
# TTSEngine
# ---------------------------------------------------------------------------
class TTSEngine:
    def __init__(self, api_key: str, voice_id: str = DEFAULT_VOICE_ID):
        self.api_key = api_key
        self.voice_id = voice_id
        self.voice_settings = dict(TTS_SETTINGS)  # copia d'istanza — aggiornabile a runtime
        self._cache: dict[str, bytes] = {}
        self._client = httpx.Client(timeout=30.0)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        log.info(f"TTS Engine inizializzato | voce: {voice_id}")

    def update_config(self, voice_id: str = None, settings: dict = None):
        """Aggiorna voce e/o parametri a runtime senza riavviare il server."""
        if voice_id:
            self.voice_id = voice_id
            log.info(f"TTS voce aggiornata: {voice_id}")
        if settings:
            self.voice_settings.update(settings)
            log.info(f"TTS settings aggiornati: {settings}")

    def _text_hash(self, text: str) -> str:
        """Hash del testo per la cache."""
        return hashlib.md5(f"{self.voice_id}:{text}".encode()).hexdigest()[:16]

    def _cache_path(self, text_hash: str) -> Path:
        return CACHE_DIR / f"{text_hash}.mp3"

    def _generate(self, text: str) -> Optional[bytes]:
        """Chiama ElevenLabs API e ritorna i bytes audio."""
        try:
            resp = self._client.post(
                f"{ELEVENLABS_API_URL}/text-to-speech/{self.voice_id}",
                headers={
                    "xi-api-key": self.api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "text": text,
                    "model_id": TTS_MODEL,
                    "voice_settings": self.voice_settings,
                },
            )
            if resp.status_code == 200:
                return resp.content
            else:
                log.error(f"ElevenLabs error {resp.status_code}: {resp.text}")
                return None
        except Exception as e:
            log.error(f"Errore chiamata TTS: {e}")
            return None

    def get_audio(self, text: str) -> Optional[bytes]:
        """
        Ritorna i bytes audio per il testo dato.
        Usa la cache in memoria, poi su disco, poi genera da API.
        """
        h = self._text_hash(text)

        # 1. Cache in memoria
        if h in self._cache:
            return self._cache[h]

        # 2. Cache su disco
        cache_file = self._cache_path(h)
        if cache_file.exists():
            audio = cache_file.read_bytes()
            self._cache[h] = audio
            return audio

        # 3. Genera da API
        log.info(f"  TTS generando: \"{text[:50]}...\"" if len(text) > 50 else f"  TTS generando: \"{text}\"")
        audio = self._generate(text)
        if audio:
            self._cache[h] = audio
            cache_file.write_bytes(audio)
            log.info(f"  TTS generato e salvato in cache ({len(audio)} bytes)")
        return audio

    def pregenerate_all(self, cues: list) -> dict[str, int]:
        """
        Pre-genera tutti gli audio per tutti i cue del copione.
        Ritorna statistiche: {generated, cached, errors}
        Chiamato all'avvio del server.
        """
        stats = {"generated": 0, "cached": 0, "errors": 0}
        total = sum(len(cue.instructions) for cue in cues)
        log.info(f"TTS pre-generazione: {total} istruzioni...")

        for cue in cues:
            for instr in cue.instructions:
                if not instr.text:
                    continue
                h = self._text_hash(instr.text)
                cache_file = self._cache_path(h)

                if h in self._cache or cache_file.exists():
                    stats["cached"] += 1
                    # Carica in memoria se non c'è già
                    if h not in self._cache and cache_file.exists():
                        self._cache[h] = cache_file.read_bytes()
                else:
                    audio = self.get_audio(instr.text)
                    if audio:
                        stats["generated"] += 1
                    else:
                        stats["errors"] += 1

        log.info(
            f"TTS completato: {stats['generated']} generati, "
            f"{stats['cached']} da cache, {stats['errors']} errori"
        )
        return stats

    def regenerate(self, text: str) -> Optional[bytes]:
        """
        Forza la rigenerazione dell'audio per un testo specifico,
        ignorando la cache. Usato quando un'istruzione viene modificata.
        """
        h = self._text_hash(text)
        # Rimuovi dalla cache
        self._cache.pop(h, None)
        cache_file = self._cache_path(h)
        if cache_file.exists():
            cache_file.unlink()
        # Rigenera
        return self.get_audio(text)

    def list_voices(self) -> list[dict]:
        """Lista le voci disponibili su ElevenLabs."""
        try:
            resp = self._client.get(
                f"{ELEVENLABS_API_URL}/voices",
                headers={"xi-api-key": self.api_key},
            )
            if resp.status_code == 200:
                voices = resp.json().get("voices", [])
                return [
                    {
                        "voice_id": v["voice_id"],
                        "name": v["name"],
                        "category": v.get("category", ""),
                        "labels": v.get("labels", {}),
                    }
                    for v in voices
                ]
        except Exception as e:
            log.error(f"Errore lista voci: {e}")
        return []

    def close(self):
        self._client.close()
