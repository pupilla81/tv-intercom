# CHANGELOG — TV Intercom

Tutte le modifiche significative al progetto sono documentate qui.
Formato: `[versione] - data - descrizione`

---

## [0.8.0] - 2026-03-20 — Nuova Dashboard Control Room

### Aggiunto
- `client-regia/dashboard.html` — nuova dashboard operativa completa, sostituisce la vecchia pagina di test/utility in `tools/`. Tre tab:
  - **HOME**: stat card (uptime, camere online, cue totali/scattati, copione), camera grid con stato ONLINE/OFFLINE in tempo reale, caricamento copione da path JSON o testo libero (conversione automatica via `/api/script/convert`), controlli STT (selezione device e engine, start/stop, rilevamento dispositivi), engine reset, log eventi WebSocket con fallback polling a 10s
  - **PROMPTER**: testo copione con cue inline cliccabili (verde=auto, ambra=manuale, click=fire immediato), cue sheet laterale con pulsante FIRE per ogni cue, navigatori per scena, scroll automatico al prossimo cue non scattato, badge tab con cue rimanenti
  - **TTS CONFIG**: griglia voci ElevenLabs con selezione, slider parametri (stability, similarity boost, style exaggeration, speaker boost), test audio in-page, rigenerazione singolo cue da testo

### Modificato
- `server/main.py` — route `/` aggiornata: dashboard servita da `client-regia/dashboard.html` invece di `tools/dashboard.html`

### Rimosso
- `tools/dashboard.html` — vecchia pagina di test/utility

---

## [0.7.0] - 2026-03-18 — PWA Audio Background + HTTPS

### Risolto
- **Problema critico audio con schermo bloccato** — causa: il base64 dell'audio silenzioso era troncato e non valido. Android non lo riconosceva come stream audio attivo e sospendeva tutto.
- **Soluzione:** file `silent.mp3` reale generato con FFmpeg sul VPS (10s di silenzio, ~9KB), servito da `/operator/silent.mp3` e caricato in cache dal Service Worker.

### Come funziona ora (approccio Spotify)
- `<audio>` HTML con `silent.mp3` in loop → Android/iOS lo riconoscono come media player nativo
- MediaSession API collegata all'audio attivo → sistema operativo non sospende mai
- Wake Lock API → schermo sempre acceso durante il servizio
- Visibility change → riconnessione automatica + replay messaggio perso al ritorno in foreground
- Service Worker → cache offline, aggiornamenti automatici

### Aggiunto
- **HTTPS** con DuckDNS + Let's Encrypt (`https://tvintercom.duckdns.org`)
- Certificato SSL gratuito, rinnovo automatico ogni 90 giorni
- WebSocket ora usa `wss://` — connessioni cifrate
- URL dinamici nella dashboard e PWA (http/https, ws/wss automatici)
- `silent.mp3` generato con FFmpeg sul VPS

### Note operative
- La PWA installata dalla schermata Home ha più privilegi del browser — usare sempre l'icona
- Il file `silent.mp3` viene scaricato una volta sola e messo in cache
- Il play parte obbligatoriamente dal click su "Entra in servizio" (requisito browser)

---

## [0.6.0] - 2026-03-16 — Deploy VPS

### Aggiunto
- **Deploy su Hetzner CCX13** (2 CPU AMD, 8GB RAM, IP: 46.225.227.204)
- **Servizio systemd** — avvio automatico al boot, restart automatico in caso di crash
- Dashboard URL dinamici — funziona sia su localhost che su IP/dominio reale
- Endpoint `/api/stt/start` e `/api/stt/stop` per controllo STT dalla dashboard
- Tasto **FERMA STT** nella dashboard con aggiornamento stato in tempo reale

### Comandi utili VPS
```bash
systemctl restart tv-intercom   # riavvia dopo git pull
journalctl -u tv-intercom -f    # log in tempo reale
git pull && systemctl restart tv-intercom  # aggiorna e riavvia
```

---

## [0.5.0] - 2026-03-16 — Convertitore Copione

### Aggiunto
- **Convertitore copione** (`tools/doc_to_script.py`)
  - Converte testo semplice (da Google Doc, Word, ecc.) in `script.json`
  - Riconosce automaticamente personaggi, battute, istruzioni camera, atti e scene
  - Convenzione semplice: `[CAM1: istruzione]` accanto alle battute
  - Supporto cue manuali con `[MANUALE: descrizione]`
  - Parametri: `--title`, `--date`, `--location`, `--preview`
  - Modalità interattiva (`--interactive`) per incollare testo direttamente
- **Copione di esempio** (`tools/copione_esempio.txt`) — riferimento formato

### Note operative
- Flusso consigliato: Google Doc → Scarica come .txt → converti → riavvia server
- Il JSON generato è compatibile direttamente con il server senza modifiche

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

- [ ] Merge branch `feature/webrtc-livekit` → `main`
- [ ] Config TTS lato server (salvataggio persistente voce e parametri)
- [ ] Dashboard: miglioramenti post primo test live
- [ ] Gestione atti/scene: caricamento segmenti, selezione scena attiva
- [ ] VU meter mic nella PWA operatore
- [ ] Feedback visivo livello mic operatore
- [ ] App nativa Android (Capacitor) per massima affidabilità
- [ ] Test iOS con TestFlight
