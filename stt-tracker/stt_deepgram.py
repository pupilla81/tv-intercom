"""
stt-tracker/stt_deepgram.py
---------------------------
STT Tracker con Deepgram — streaming WebSocket in tempo reale.
Latenza ~300ms. Usa websockets direttamente (compatibile con SDK v6+).

Utilizzo:
    python stt_deepgram.py --list-devices
    python stt_deepgram.py --device 7 --server http://localhost:8000
"""

import argparse
import asyncio
import json
import queue
import sys
import threading
import time
from typing import Optional

import httpx
import numpy as np
import sounddevice as sd
import websockets

# ---------------------------------------------------------------------------
# Configurazione
# ---------------------------------------------------------------------------
SAMPLE_RATE = 16000
LANGUAGE = "it"
MODEL = "nova-2"
DEEPGRAM_WS_URL = (
    f"wss://api.deepgram.com/v1/listen"
    f"?model={MODEL}"
    f"&language={LANGUAGE}"
    f"&encoding=linear16"
    f"&sample_rate={SAMPLE_RATE}"
    f"&channels=1"
    f"&interim_results=true"
    f"&smart_format=true"
    f"&utterance_end_ms=1000"
    f"&vad_events=true"
)


# ---------------------------------------------------------------------------
# Lista periferiche
# ---------------------------------------------------------------------------
def list_audio_devices() -> list[dict]:
    devices = sd.query_devices()
    inputs = []
    for i, dev in enumerate(devices):
        if dev["max_input_channels"] > 0:
            inputs.append({
                "index": i,
                "name": dev["name"],
                "channels": dev["max_input_channels"],
                "sample_rate": int(dev["default_samplerate"]),
                "is_default": i == sd.default.device[0],
            })
    return inputs


def print_devices():
    devices = list_audio_devices()
    print("\n📻 Periferiche audio di input disponibili:\n")
    for d in devices:
        default_str = " ← DEFAULT" if d["is_default"] else ""
        print(f"  [{d['index']:2d}] {d['name']}")
        print(f"       Canali: {d['channels']} | Sample rate: {d['sample_rate']} Hz{default_str}")
    print()
    return devices


def select_device_interactive() -> int:
    devices = print_devices()
    default_idx = next((d["index"] for d in devices if d["is_default"]), devices[0]["index"])
    while True:
        try:
            choice = input(f"Seleziona periferica [{default_idx}]: ").strip()
            if choice == "":
                return default_idx
            idx = int(choice)
            if any(d["index"] == idx for d in devices):
                return idx
            print(f"  ⚠️  Indice {idx} non valido.")
        except ValueError:
            print("  ⚠️  Inserisci un numero intero.")
        except KeyboardInterrupt:
            sys.exit(0)


# ---------------------------------------------------------------------------
# Ricampionamento
# ---------------------------------------------------------------------------
def resample(audio: np.ndarray, orig_rate: int, target_rate: int) -> np.ndarray:
    if orig_rate == target_rate:
        return audio
    new_length = int(len(audio) * target_rate / orig_rate)
    return np.interp(
        np.linspace(0, len(audio) - 1, new_length),
        np.arange(len(audio)),
        audio,
    ).astype(np.float32)


# ---------------------------------------------------------------------------
# Deepgram STT Tracker
# ---------------------------------------------------------------------------
class DeepgramSTTTracker:
    def __init__(
        self,
        device_index: int,
        server_url: str,
        api_key: str,
        language: str = LANGUAGE,
    ):
        self.device_index = device_index
        self.server_url = server_url.rstrip("/")
        self.api_key = api_key
        self.language = language

        self._running = False
        self._audio_queue: queue.Queue = queue.Queue()
        self._http_client = httpx.Client(timeout=5.0)
        self._stats = {
            "transcriptions": 0,
            "cues_fired": 0,
            "errors": 0,
            "start_time": None,
        }

        dev_info = sd.query_devices(device_index)
        self._device_rate = int(dev_info["default_samplerate"])
        self._needs_resample = self._device_rate != SAMPLE_RATE
        if self._needs_resample:
            print(f"  ℹ️  Ricampionamento: {self._device_rate}Hz → {SAMPLE_RATE}Hz")

    def _audio_callback(self, indata, frames, time_info, status):
        if status:
            print(f"  ⚠️  {status}")
        audio = indata[:, 0].copy()
        if self._needs_resample:
            audio = resample(audio, self._device_rate, SAMPLE_RATE)
        # Converti float32 → int16 per Deepgram
        audio_int16 = np.clip(audio * 32767, -32768, 32767).astype(np.int16)
        self._audio_queue.put(audio_int16.tobytes())

    def _send_to_server(self, text: str):
        try:
            resp = self._http_client.post(
                f"{self.server_url}/api/stt/chunk",
                json={"text": text},
            )
            result = resp.json()
            fired = result.get("fired", [])
            if fired:
                self._stats["cues_fired"] += len(fired)
                for cue_id in fired:
                    print(f"  🔔 CUE SCATTATO: {cue_id}")
        except Exception as e:
            print(f"  ❌ Errore server: {e}")
            self._stats["errors"] += 1

    def _check_stop_flag(self) -> bool:
        """Controlla se il server ha segnalato di fermarsi."""
        try:
            resp = self._http_client.get(f"{self.server_url}/api/status", timeout=2.0)
            data = resp.json()
            return not data.get("stt_active", True)
        except Exception:
            return False

    async def _stop_check_loop(self):
        """Controlla ogni 5s se la dashboard ha richiesto lo stop."""
        while self._running:
            await asyncio.sleep(5)
            if self._check_stop_flag():
                print("\n  ⏹  Stop ricevuto dalla dashboard.")
                self._running = False
                break

    async def _send_audio_loop(self, ws):
        """Invia continuamente audio a Deepgram."""
        while self._running:
            try:
                data = self._audio_queue.get(timeout=0.3)
                await ws.send(data)
            except queue.Empty:
                continue
            except Exception as e:
                if self._running:
                    print(f"  ❌ Errore invio audio: {e}")
                break

    async def _receive_loop(self, ws):
        """Riceve trascrizioni da Deepgram."""
        async for message in ws:
            try:
                msg = json.loads(message)
                msg_type = msg.get("type", "")

                if msg_type == "Results":
                    channel = msg.get("channel", {})
                    alts = channel.get("alternatives", [{}])
                    transcript = alts[0].get("transcript", "").strip()
                    is_final = msg.get("is_final", False)

                    if not transcript:
                        continue

                    if is_final:
                        self._stats["transcriptions"] += 1
                        print(f"  🎙  STT: \"{transcript}\"")
                        self._send_to_server(transcript)
                    else:
                        print(f"  ···  {transcript:<60}", end="\r")

                elif msg_type == "UtteranceEnd":
                    print(" " * 65, end="\r")  # pulisci riga parziale

                elif msg_type == "Metadata":
                    pass  # ignora metadata

            except Exception as e:
                print(f"  ❌ Errore parsing risposta: {e}")

    async def start(self):
        dev = sd.query_devices(self.device_index)
        print(f"\n🎤 Periferica: [{self.device_index}] {dev['name']}")
        print(f"   Sample rate nativo: {self._device_rate}Hz")
        print(f"🌐 Server intercom: {self.server_url}")
        print(f"🔤 Lingua: {self.language} | Modello: {MODEL}")
        print(f"\n⏳ Connessione a Deepgram...")

        ws_url = (
            f"wss://api.deepgram.com/v1/listen"
            f"?model={MODEL}"
            f"&language={self.language}"
            f"&encoding=linear16"
            f"&sample_rate={SAMPLE_RATE}"
            f"&channels=1"
            f"&interim_results=true"
            f"&smart_format=true"
            f"&utterance_end_ms=1000"
            f"&vad_events=true"
        )

        headers = {"Authorization": f"Token {self.api_key}"}

        try:
            async with websockets.connect(ws_url, additional_headers=headers) as ws:
                print(f"✅ Deepgram connesso.\n")
                print(f"▶  In ascolto... (Ctrl+C per fermare)\n")

                self._running = True
                self._stats["start_time"] = time.time()

                # Avvia acquisizione audio in thread separato
                stream = sd.InputStream(
                    samplerate=self._device_rate,
                    device=self.device_index,
                    channels=1,
                    dtype="float32",
                    blocksize=int(self._device_rate * 0.1),
                    callback=self._audio_callback,
                )
                stream.start()

                try:
                    # Esegui send, receive e check stop in parallelo
                    await asyncio.gather(
                        self._send_audio_loop(ws),
                        self._receive_loop(ws),
                        self._stop_check_loop(),
                    )
                except asyncio.CancelledError:
                    pass
                finally:
                    stream.stop()
                    stream.close()
                    # Segnala fine stream a Deepgram
                    try:
                        await ws.send(json.dumps({"type": "CloseStream"}))
                    except Exception:
                        pass

        except websockets.exceptions.InvalidStatus as e:
            print(f"\n❌ Errore autenticazione Deepgram: {e}")
            print("   Verifica che la API key sia corretta.")
        except Exception as e:
            print(f"\n❌ Errore connessione: {e}")
        finally:
            self.stop()

    def stop(self):
        self._running = False
        elapsed = int(time.time() - (self._stats["start_time"] or time.time()))
        print(f"\n\n⏹  STT Tracker fermato.")
        print(f"   Durata: {elapsed}s")
        print(f"   Trascrizioni finali: {self._stats['transcriptions']}")
        print(f"   Cue scattati: {self._stats['cues_fired']}")
        print(f"   Errori: {self._stats['errors']}")
        self._http_client.close()


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
def main():
    import os

    parser = argparse.ArgumentParser(description="TV Intercom — STT Tracker (Deepgram)")
    parser.add_argument("--list-devices", action="store_true")
    parser.add_argument("--device", type=int, default=None)
    parser.add_argument("--server", type=str, default="http://localhost:8000")
    parser.add_argument("--language", type=str, default=LANGUAGE)
    parser.add_argument(
        "--api-key", type=str,
        default=os.environ.get("DEEPGRAM_API_KEY", ""),
    )
    args = parser.parse_args()

    if args.list_devices:
        print_devices()
        return

    if not args.api_key:
        print("❌ API key Deepgram mancante.")
        print("   Usa --api-key oppure: set DEEPGRAM_API_KEY=la-tua-key")
        sys.exit(1)

    device_index = args.device
    if device_index is None:
        device_index = select_device_interactive()

    tracker = DeepgramSTTTracker(
        device_index=device_index,
        server_url=args.server,
        api_key=args.api_key,
        language=args.language,
    )

    try:
        asyncio.run(tracker.start())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
