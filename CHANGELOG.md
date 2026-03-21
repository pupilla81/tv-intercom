## [0.10.0] - 2026-03-21 — STT browser, prompter testo completo, fix engine

### Aggiunto
- `main.py`: `GET /api/stt/token` — restituisce API key Deepgram per uso browser
- `main.py`: `GET /api/script/lines` — restituisce script_lines con cue risolti inline per il prompter
- `main.py`: loader `_load_new_format` espone `script_lines` in `AppState`
- `dashboard.html`: pannello STT con VU meter browser, enumerazione dispositivi locali, AVVIA AUTO (Deepgram JS), CLI ▾ con comando pronto e campo device number manuale
- `dashboard.html`: prompter testo completo — personaggi, dialoghi, note regia, cue card inline cliccabili con istruzioni camera

### Modificato
- `main.py`: `POST /api/stt/stop?source=browser` — non tocca `stt_active` e non notifica CLI quando a fermarsi è il browser
- `main.py`: `POST /api/stt/chunk` — aggiunto try/except, restituisce sempre JSON valido
- `main.py`: `_load_new_format` — aggiunto `match_threshold` al trigger (default `0.75`, gestisce `None`)
- `tools/doc_to_script.py` — trigger auto include `match_threshold: 0.75`, trigger manuale include `match_threshold: None`
- `client-regia/intercom-board.html`: fix `publishTrack` — rimossa opzione `muted:true` non supportata nelle versioni recenti di LiveKit, sostituita con publish + delay 300ms + mute esplicito (fix anche in operator-livekit.html)
- `dashboard.html`: STT browser usa AudioWorklet 16kHz + fallback ScriptProcessor, MediaRecorder rimosso

### Fix
- `publishTrack {muted:true}` causava disconnect immediato da LiveKit — fix in intercom-board e operator-livekit
- `match_threshold` mancante nel SimpleNamespace causava crash su ogni chunk STT
- STT browser si fermava subito per loop `onclose→sttStopAuto` — fix con guard `sttActive=false` prima di `close()`
- `stt_stopped` WebSocket interferiva con browser STT attivo — ora controlla `sttActive` prima di aggiornare UI
- Vecchi JSON sul server con `match_threshold: null` ora gestiti con fallback `or 0.75`
