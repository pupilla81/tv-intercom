"""
stt-tracker/stt_tracker.py
--------------------------
Modulo STT — ascolta l'audio da una periferica di input (fisica o virtuale)
e manda i chunk di testo trascritti al server via HTTP.

Utilizzo:
    # Lista periferiche disponibili
    python stt_tracker.py --list-devices

    # Avvio con periferica specifica
    python stt_tracker.py --device 2 --server http://localhost:8000

    # Avvio interattivo (chiede la periferica all'avvio)
    python stt_tracker.py --server http://localhost:8000
"""

import argparse
import queue
import sys
import threading
import time
from typing import Optional

import httpx
import numpy as np
import sounddevice as sd
import whisper

# ---------------------------------------------------------------------------
# Configurazione
# ---------------------------------------------------------------------------
SAMPLE_RATE = 16000          # Whisper richiede 16kHz
CHUNK_SECONDS = 2            # Durata di ogni chunk da trascrivere
OVERLAP_SECONDS = 1          # Sovrapposizione tra chunk (evita parole tagliate)
SILENCE_THRESHOLD = 0.001     # Sotto questa soglia il chunk viene ignorato
WHISPER_MODEL = "base"       # "base" = veloce, "large-v3" = più preciso
LANGUAGE = "it"              # Lingua del copione


# ---------------------------------------------------------------------------
# Ricampionamento
# ---------------------------------------------------------------------------
def resample(audio: np.ndarray, orig_rate: int, target_rate: int) -> np.ndarray:
    """Ricampiona l'audio da orig_rate a target_rate con interpolazione lineare."""
    if orig_rate == target_rate:
        return audio
    ratio = target_rate / orig_rate
    new_length = int(len(audio) * ratio)
    return np.interp(
        np.linspace(0, len(audio) - 1, new_length),
        np.arange(len(audio)),
        audio,
    ).astype(np.float32)


# ---------------------------------------------------------------------------
# Lista periferiche
# ---------------------------------------------------------------------------
def list_audio_devices() -> list[dict]:
    """Restituisce la lista delle periferiche di input disponibili."""
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
    """Stampa le periferiche di input in formato leggibile."""
    devices = list_audio_devices()
    print("\n📻 Periferiche audio di input disponibili:\n")
    for d in devices:
        default_str = " ← DEFAULT" if d["is_default"] else ""
        print(f"  [{d['index']:2d}] {d['name']}")
        print(f"       Canali: {d['channels']} | Sample rate: {d['sample_rate']} Hz{default_str}")
    print()
    return devices


def select_device_interactive() -> int:
    """Mostra la lista e chiede all'utente di scegliere."""
    devices = print_devices()
    default_idx = next((d["index"] for d in devices if d["is_default"]), devices[0]["index"])

    while True:
        try:
            choice = input(f"Seleziona il numero della periferica [{default_idx}]: ").strip()
            if choice == "":
                return default_idx
            idx = int(choice)
            if any(d["index"] == idx for d in devices):
                return idx
            print(f"  ⚠️  Indice {idx} non valido, riprova.")
        except ValueError:
            print("  ⚠️  Inserisci un numero intero.")
        except KeyboardInterrupt:
            print("\nAnnullato.")
            sys.exit(0)


# ---------------------------------------------------------------------------
# STT Engine
# ---------------------------------------------------------------------------
class STTTracker:
    def __init__(
        self,
        device_index: int,
        server_url: str,
        model_name: str = WHISPER_MODEL,
        language: str = LANGUAGE,
        on_transcription=None,
    ):
        self.device_index = device_index
        self.server_url = server_url.rstrip("/")
        self.language = language
        self.on_transcription = on_transcription

        self._audio_queue: queue.Queue = queue.Queue()
        self._running = False
        self._buffer = np.array([], dtype=np.float32)

        # Rileva sample rate nativo del dispositivo e ricampiona se necessario
        dev_info = sd.query_devices(device_index)
        self._device_rate = int(dev_info["default_samplerate"])
        self._needs_resample = self._device_rate != SAMPLE_RATE
        if self._needs_resample:
            print(f"  ℹ️  Ricampionamento: {self._device_rate}Hz → {SAMPLE_RATE}Hz")

        self._chunk_size = CHUNK_SECONDS * SAMPLE_RATE
        self._overlap_size = OVERLAP_SECONDS * SAMPLE_RATE
        self._client = httpx.Client(timeout=5.0)
        self._stats = {
            "chunks_processed": 0,
            "cues_fired": 0,
            "errors": 0,
            "start_time": None,
        }

        print(f"\n⏳ Caricamento modello Whisper '{model_name}'...")
        self.model = whisper.load_model(model_name)
        print(f"✅ Modello caricato.\n")

    def _audio_callback(self, indata, frames, time_info, status):
        """Chiamata da sounddevice per ogni blocco audio acquisito."""
        if status:
            print(f"  ⚠️  Audio status: {status}")
        self._audio_queue.put(indata[:, 0].copy())  # mono

    def _is_silent(self, audio: np.ndarray) -> bool:
        return float(np.abs(audio).mean()) < SILENCE_THRESHOLD

    def _transcribe(self, audio: np.ndarray) -> Optional[str]:
        """Trascrive un chunk audio con Whisper."""
        try:
            result = self.model.transcribe(
                audio,
                language=self.language,
                fp16=False,
                condition_on_previous_text=False,
            )
            text = result["text"].strip()
            return text if text else None
        except Exception as e:
            print(f"  ❌ Errore Whisper: {e}")
            self._stats["errors"] += 1
            return None

    def _send_to_server(self, text: str) -> dict:
        """Manda il testo trascritto al server."""
        try:
            resp = self._client.post(
                f"{self.server_url}/api/stt/chunk",
                json={"text": text},
            )
            return resp.json()
        except Exception as e:
            print(f"  ❌ Errore server: {e}")
            self._stats["errors"] += 1
            return {}

    def _process_loop(self):
        """Loop di elaborazione: accumula audio, trascrive, manda al server."""
        while self._running:
            try:
                chunk = self._audio_queue.get(timeout=0.5)

                # Ricampiona se necessario
                if self._needs_resample:
                    chunk = resample(chunk, self._device_rate, SAMPLE_RATE)

                self._buffer = np.concatenate([self._buffer, chunk])

                if len(self._buffer) >= self._chunk_size:
                    audio_chunk = self._buffer[:self._chunk_size].copy()
                    # Mantieni overlap per il prossimo chunk
                    self._buffer = self._buffer[self._chunk_size - self._overlap_size:]

                    if self._is_silent(audio_chunk):
                        continue

                    text = self._transcribe(audio_chunk)
                    if not text:
                        continue

                    self._stats["chunks_processed"] += 1
                    print(f"  🎙  STT: \"{text}\"")

                    if self.on_transcription:
                        self.on_transcription(text)

                    result = self._send_to_server(text)
                    fired = result.get("fired", [])
                    if fired:
                        self._stats["cues_fired"] += len(fired)
                        for cue_id in fired:
                            print(f"  🔔 CUE SCATTATO: {cue_id}")

            except queue.Empty:
                continue
            except Exception as e:
                print(f"  ❌ Errore nel loop: {e}")
                self._stats["errors"] += 1

    def start(self):
        """Avvia la registrazione e il processing."""
        dev = sd.query_devices(self.device_index)
        print(f"🎤 Periferica: [{self.device_index}] {dev['name']}")
        print(f"   Sample rate nativo: {self._device_rate}Hz")
        print(f"🌐 Server: {self.server_url}")
        print(f"🔤 Lingua: {self.language}")
        print(f"⏱  Chunk: {CHUNK_SECONDS}s | Overlap: {OVERLAP_SECONDS}s")
        print(f"\n▶  In ascolto... (Ctrl+C per fermare)\n")

        self._running = True
        self._stats["start_time"] = time.time()

        process_thread = threading.Thread(target=self._process_loop, daemon=True)
        process_thread.start()

        try:
            with sd.InputStream(
                samplerate=self._device_rate,   # usa il rate nativo del dispositivo
                device=self.device_index,
                channels=1,
                dtype="float32",
                blocksize=int(self._device_rate * 0.5),
                callback=self._audio_callback,
            ):
                while self._running:
                    time.sleep(0.1)
        except KeyboardInterrupt:
            self.stop()
        except Exception as e:
            print(f"\n❌ Errore periferica audio: {e}")
            self.stop()

    def stop(self):
        """Ferma la registrazione e stampa le statistiche."""
        self._running = False
        elapsed = int(time.time() - (self._stats["start_time"] or time.time()))
        print(f"\n\n⏹  STT Tracker fermato.")
        print(f"   Durata: {elapsed}s")
        print(f"   Chunk elaborati: {self._stats['chunks_processed']}")
        print(f"   Cue scattati: {self._stats['cues_fired']}")
        print(f"   Errori: {self._stats['errors']}")
        self._client.close()


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="TV Intercom — STT Tracker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Esempi:
  python stt_tracker.py --list-devices
  python stt_tracker.py --device 8 --server http://localhost:8000
  python stt_tracker.py --device 7 --server http://IP-VPS:8000 --model large-v3
        """,
    )
    parser.add_argument(
        "--list-devices", action="store_true",
        help="Mostra le periferiche audio disponibili ed esci"
    )
    parser.add_argument(
        "--device", type=int, default=None,
        help="Indice della periferica audio di input (vedi --list-devices)"
    )
    parser.add_argument(
        "--server", type=str, default="http://localhost:8000",
        help="URL del server TV Intercom (default: http://localhost:8000)"
    )
    parser.add_argument(
        "--model", type=str, default=WHISPER_MODEL,
        choices=["tiny", "base", "small", "medium", "large-v3"],
        help=f"Modello Whisper da usare (default: {WHISPER_MODEL})"
    )
    parser.add_argument(
        "--language", type=str, default=LANGUAGE,
        help=f"Codice lingua (default: {LANGUAGE})"
    )

    args = parser.parse_args()

    if args.list_devices:
        print_devices()
        return

    device_index = args.device
    if device_index is None:
        device_index = select_device_interactive()

    tracker = STTTracker(
        device_index=device_index,
        server_url=args.server,
        model_name=args.model,
        language=args.language,
    )
    tracker.start()


if __name__ == "__main__":
    main()
