# CHANGELOG — TV Intercom

Tutte le modifiche significative al progetto sono documentate qui.
Formato: `[versione] - data - descrizione`

---

## [0.4.0] - 2026-03-15 — STT Deepgram Streaming

### Aggiunto
- **STT Tracker Deepgram** (`stt-tracker/stt_deepgram.py`)
  - Streaming WebSocket diretto con Deepgram API (senza dipendenza dall'SDK v6)
  - Latenza ~2s in locale (vs ~3-4s di Whisper base)
  - Risultati parziali in tempo reale durante il parlato
  - Rilevamento fine frase con `utterance_end_ms=1000`
  - Ricampionamento automatico sample rate nativo → 16kHz
  - Compatibile con Deepgram SDK v6.0.1+

### Note tecniche
- Implementazione diretta via `websockets` — non usa le classi dell'SDK
  che sono cambiate significativamente nella v6
- Whisper locale (`stt_tracker.py`) rimane disponibile come alternativa offline

---

## [0.3.0] - 2026-03-15 — TTS e Audio Operatore

### Aggiunto
- **TTS Engine** (`server/tts_engine.py`) con ElevenLabs API
  - Pre-generazione di tutti gli audio all'avvio del server
  - Cache su disco — gli audio non vengono rigenerati ai riavvii successivi
  - Rigenerazione singola per istruzioni modificate durante l'evento
- **Endpoint `/api/tts/test-audio`** — genera audio di test per verifica cuffie
- **Endpoint `/api/tts/voices`** — lista voci disponibili su ElevenLabs
- **Endpoint `/api/tts/regenerate`** — rigenera un audio specifico
- **Schermata di avvio PWA** con:
  - Test cuffie con voce TTS reale (stesso livello delle istruzioni)
  - Slider volume regolabile in tempo reale durante il test
  - Pulsante "Entra in servizio" — sblocca audio e connette al server
- **Visualizzazione testo istruzione** sulla PWA operatore in tempo reale

### Modificato
- `server/main.py` — integrazione TTS Engine, nuovo endpoint test audio
- `client-operator/index.html` — overlay avvio, test audio, controllo volume

---

## [0.2.0] - 2026-03-xx — Server, PWA Operatore, STT Tracker

### Aggiunto
- **Server FastAPI** (`server/main.py`) con:
  - WebSocket per operatori camera (connessione, disconnessione, replay)
  - WebSocket per pannello regia (notifiche in tempo reale)
  - API REST: carica copione, scatta cue, reset motore, chunk STT
  - Invio istruzioni in parallelo a più camere simultaneamente
  - Fallback testo quando audio non disponibile
  - Replay ultimo messaggio per camera
- **PWA Operatore** (`client-operator/index.html`)
  - Connessione WebSocket automatica con riconnessione
  - Tasto Replay grande e centrale
  - Indicatori stato connessione e audio
  - Log messaggi con timestamp
  - Selezione camera via parametro URL `?cam=N`
- **STT Tracker** (`stt-tracker/stt_tracker.py`)
  - Lista periferiche audio disponibili (`--list-devices`)
  - Selezione periferica interattiva o da parametro
  - Rilevamento automatico sample rate nativo e ricampionamento a 16kHz
  - Finestra scorrevole sugli ultimi 5 chunk per gestire frasi spezzettate
  - Invio chunk trascritti al server via HTTP
  - Statistiche a fine sessione
- **`avvia_server.bat`** — script di avvio rapido per Windows (sul desktop)

### Modificato
- `script-parser/cue_engine.py` — aggiunta finestra scorrevole per matching frasi spezzettate

---

## [0.1.0] - 2026-03-xx — Struttura base e Modulo Script Parser

### Aggiunto
- Struttura repository con cartelle: `docs/`, `script-parser/`, `server/`, `client-operator/`, `client-regia/`, `stt-tracker/`
- **Documentazione architetturale** (`docs/architettura.md`) con decisioni fissate
- **Specifiche formato copione** (`docs/cue-format.md`)
- **Script Parser** (`script-parser/script_parser.py`)
  - Caricamento e validazione copione JSON
  - Separazione cue automatici e manuali
  - Sommario leggibile del copione
- **Cue Engine** (`script-parser/cue_engine.py`)
  - Fuzzy matching con `rapidfuzz` (token_set_ratio)
  - Puntatore in avanti (no backtrack)
  - Callback `on_cue_fired` per il dispatcher
  - `force_fire()` per trigger manuale dalla regia
  - Reset per prove e repliche
- **Copione di esempio** (`script-parser/sample_script.json`)
  - 2 atti, 4 scene, 7 cue (5 automatici, 2 manuali), 5 camere
- **Pannello Regia prototipo** (`client-regia/intercom-regia.jsx`)
  - Tasti canale 1-6 con tastiera/tastierino numerico
  - Modalità PTT e Toggle
  - Master mute
  - Log trasmissioni con timestamp

### Infrastruttura
- `README.md` con stato moduli e quick start
- `requirements.txt` globale
- `docs/avvio-deploy.md` con istruzioni per sviluppo locale e VPS

---

## Decisioni architetturali fissate

| Decisione | Scelta |
|---|---|
| Comunicazione umana | Discord sempre attivo + PTT Bluetooth |
| Istruzioni automatiche | WebRTC/WebSocket custom su VPS |
| Rete in esterna | SIM dati individuali → VPS cloud |
| TTS | ElevenLabs multilingual v2, pre-generato all'avvio |
| STT | Whisper (locale), ricampionamento automatico |
| Replay | Tasto secondario Bluetooth, ultimo audio in memoria |
| Interfaccia operatore | PWA — nessuna installazione, funziona su iOS e Android |

---

## Prossimi passi pianificati

- [ ] Riduzione latenza STT (utterance_end_ms, chunk size, VPS)
- [ ] Dashboard regia completa (pannello cue, stato camere, controlli)
- [ ] Sezione impostazioni (periferica audio, URL server, voce TTS)
- [ ] Deploy su VPS con istruzioni complete
- [ ] Test in esterna con SIM dati
- [ ] App desktop Electron per la regia
- [ ] Integrazione Discord PTT
