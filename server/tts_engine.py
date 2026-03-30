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
import time
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
        self._cache: dict[str, bytes] = {}  # hash del testo → bytes WAV
        self._client = httpx.Client(timeout=30.0)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        # Conta file in cache su disco
        cached_files = list(CACHE_DIR.glob("*.mp3"))
        log.info(f"TTS Engine inizializzato | voce={voice_id} | "
                 f"model={TTS_MODEL} | cache_dir={CACHE_DIR} | "
                 f"file in cache: {len(cached_files)}")

    def _text_hash(self, text: str) -> str:
        """Hash del testo per la cache."""
        return hashlib.md5(f"{self.voice_id}:{text}".encode()).hexdigest()[:16]

    def _cache_path(self, text_hash: str) -> Path:
        return CACHE_DIR / f"{text_hash}.mp3"

    def _generate(self, text: str) -> Optional[bytes]:
        """Chiama ElevenLabs API e ritorna i bytes audio."""
        t0 = time.time()
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
                    "voice_settings": TTS_SETTINGS,
                },
            )
            elapsed = (time.time() - t0) * 1000
            if resp.status_code == 200:
                log.debug(f"  ElevenLabs API OK: {len(resp.content)}B in {elapsed:.0f}ms")
                return resp.content
            else:
                log.error(f"  ElevenLabs API {resp.status_code} dopo {elapsed:.0f}ms: "
                          f"{resp.text[:200]}")
                return None
        except httpx.TimeoutException as e:
            elapsed = (time.time() - t0) * 1000
            log.error(f"  ElevenLabs TIMEOUT dopo {elapsed:.0f}ms: {e}")
            return None
        except Exception as e:
            elapsed = (time.time() - t0) * 1000
            log.error(f"  ElevenLabs ERRORE dopo {elapsed:.0f}ms: {e}", exc_info=True)
            return None

    def get_audio(self, text: str) -> Optional[bytes]:
        """
        Ritorna i bytes audio per il testo dato.
        Usa la cache in memoria, poi su disco, poi genera da API.
        """
        h = self._text_hash(text)
        short = text[:40] + ('...' if len(text) > 40 else '')

        # 1. Cache in memoria
        if h in self._cache:
            log.debug(f"  TTS cache MEM hit [{h}]: '{short}'")
            return self._cache[h]

        # 2. Cache su disco
        cache_file = self._cache_path(h)
        if cache_file.exists():
            audio = cache_file.read_bytes()
            self._cache[h] = audio
            log.debug(f"  TTS cache DISCO hit [{h}]: '{short}' ({len(audio)}B)")
            return audio

        # 3. Genera da API
        log.info(f"  TTS genera da API [{h}]: '{short}'")
        audio = self._generate(text)
        if audio:
            self._cache[h] = audio
            cache_file.write_bytes(audio)
            log.info(f"  TTS salvato in cache [{h}]: {len(audio)}B")
        else:
            log.warning(f"  TTS generazione fallita [{h}]: '{short}'")
        return audio

    def pregenerate_all(self, cues: list) -> dict[str, int]:
        """
        Pre-genera tutti gli audio per tutti i cue del copione.
        Ritorna statistiche: {generated, cached, errors}
        Chiamato all'avvio del server.
        """
        stats = {"generated": 0, "cached": 0, "errors": 0}
        total = sum(len(cue.instructions) for cue in cues)
        t0 = time.time()
        log.info(f"TTS pre-generazione: {total} istruzioni da {len(cues)} cue...")

        done = 0
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

                done += 1
                # Progress ogni 10 istruzioni generate (non cached)
                if stats["generated"] > 0 and stats["generated"] % 10 == 0:
                    elapsed = time.time() - t0
                    log.info(f"  TTS progresso: {done}/{total} "
                             f"(gen={stats['generated']}, cache={stats['cached']}, "
                             f"err={stats['errors']}) — {elapsed:.1f}s")

        elapsed = time.time() - t0
        log.info(
            f"TTS pre-generazione completata in {elapsed:.1f}s: "
            f"{stats['generated']} generati, {stats['cached']} da cache, "
            f"{stats['errors']} errori | cache mem: {len(self._cache)} voci"
        )
        return stats

    def regenerate(self, text: str) -> Optional[bytes]:
        """
        Forza la rigenerazione dell'audio per un testo specifico,
        ignorando la cache. Usato quando un'istruzione viene modificata.
        """
        h = self._text_hash(text)
        short = text[:40] + ('...' if len(text) > 40 else '')
        log.info(f"TTS regenerate [{h}]: '{short}'")
        # Rimuovi dalla cache
        self._cache.pop(h, None)
        cache_file = self._cache_path(h)
        if cache_file.exists():
            cache_file.unlink()
            log.debug(f"  Cache disco rimossa [{h}]")
        # Rigenera
        return self.get_audio(text)

    def list_voices(self) -> list[dict]:
        """Lista le voci disponibili su ElevenLabs."""
        log.debug("Richiesta lista voci ElevenLabs...")
        try:
            t0 = time.time()
            resp = self._client.get(
                f"{ELEVENLABS_API_URL}/voices",
                headers={"xi-api-key": self.api_key},
            )
            elapsed = (time.time() - t0) * 1000
            if resp.status_code == 200:
                voices = resp.json().get("voices", [])
                log.info(f"Lista voci: {len(voices)} disponibili ({elapsed:.0f}ms)")
                return [
                    {
                        "voice_id": v["voice_id"],
                        "name": v["name"],
                        "category": v.get("category", ""),
                        "labels": v.get("labels", {}),
                    }
                    for v in voices
                ]
            else:
                log.error(f"Lista voci fallita: {resp.status_code} ({elapsed:.0f}ms)")
        except Exception as e:
            log.error(f"Errore lista voci: {e}", exc_info=True)
        return []

    def close(self):
        self._client.close()
