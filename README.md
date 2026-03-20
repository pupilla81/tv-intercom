# 📡 TV Intercom — Sistema di Assistenza Regia

Sistema modulare per la comunicazione automatica e manuale tra regia e operatori camera durante eventi televisivi in esterna.

---

## Stato del progetto

| Modulo | Stato |
|---|---|
| `server/main.py` — Hub FastAPI centrale | ✅ Pronto |
| `server/tts_engine.py` — TTS ElevenLabs con cache | ✅ Pronto |
| `server/livekit_manager.py` — Token e room LiveKit | ✅ Pronto |
| `script-parser/` — Parser copione + Cue Engine | ✅ Pronto |
| `stt-tracker/stt_deepgram.py` — STT streaming ~2s | ✅ Pronto |
| `stt-tracker/stt_tracker.py` — STT Whisper (fallback) | ✅ Pronto |
| `client-operator/index.html` — PWA istruzioni operatore | ✅ Pronto |
| `client-operator/operator-livekit.html` — PWA voce LiveKit | ✅ Pronto |
| `client-regia/intercom-board.html` — Pannello regia LiveKit | ✅ Pronto |
| `client-regia/dashboard.html` — Control Room dashboard | ✅ Pronto |
| `tools/doc_to_script.py` — Convertitore copione testo→JSON | ✅ Pronto |

---

## Quick Start

```bash
# Installare dipendenze Python
pip install -r requirements.txt

# Avviare il server in locale
uvicorn server.main:app --host 0.0.0.0 --port 8080 --reload

# Avviare STT (su macchina con microfono)
python stt-tracker/stt_deepgram.py --device 7 --server https://tvintercom.duckdns.org

# Convertire copione da testo
python tools/doc_to_script.py --input copione.txt --title "Nome spettacolo"
```

---

## Deploy VPS

```bash
# Aggiorna e riavvia
cd ~/tv-intercom && git pull && systemctl restart tv-intercom

# Log in tempo reale
journalctl -u tv-intercom -f
journalctl -u livekit -f
```

---

## Documentazione

- [`CHANGELOG.md`](CHANGELOG.md) — Storico modifiche
- [`TV_INTERCOM_ARCHITETTURA.md`](TV_INTERCOM_ARCHITETTURA.md) — Architettura dettagliata e stato funzionalità
- [`docs/cue-format.md`](docs/cue-format.md) — Formato copione e cue
- [`docs/avvio-deploy.md`](docs/avvio-deploy.md) — Istruzioni sviluppo locale e VPS
