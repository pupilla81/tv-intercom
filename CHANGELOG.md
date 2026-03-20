## [0.9.0] - 2026-03-20 — Gestione copione, scene e fix endpoint

### Aggiunto
- `main.py`: endpoint `POST /api/engine/goto-scene` — sposta il puntatore STT all'inizio della scena selezionata
- `main.py`: endpoint `DELETE /api/script/clear` — cancella il JSON del copione e tutta la cache TTS su disco + reset stato in memoria
- `main.py`: endpoint `GET /api/scripts` — lista i file JSON disponibili in `script-parser/`
- `main.py`: endpoint `GET /api/script/download` — scarica il JSON del copione attivo con nome file dal titolo
- `main.py`: endpoint `POST /api/script/upload-file` — carica un JSON dal browser via multipart/form-data
- `main.py`: endpoint `POST /api/tts/config` — aggiorna voce e parametri TTS (stability, similarity, style, speaker boost) a runtime
- `main.py`: endpoint `POST /api/tts/pregenerate` — rigenera tutti gli audio TTS per il copione corrente
- `main.py`: loader nativo `_load_new_format()` per il nuovo formato JSON (scene senza atti)
- `tools/doc_to_script.py`: riscritto — solo scene (ATTO ignorato), titolo auto dalla prima riga, `scene_name` nel JSON, nome file dal titolo

### Modificato
- `client-regia/dashboard.html`:
  - Camera bar fissa tra topbar e tabs, visibile in tutti i tab
  - Tasto INTERCOM ↗ spostato nella camera bar
  - Pulsante 🗑 CANCELLA copione+cache TTS
  - Selettore scena semplificato (niente più selettore atto)
  - "Carica file" → upload JSON da PC (file picker)
  - Lista copioni sul server aggiornata dopo ogni conversione
  - `renderScript` senza intestazioni ATTO, solo SCENA con nome e anchor per scroll
- `main.py`:
  - `api_script_convert`: salva JSON con nome dal titolo (es. `passio_jesu_christi.json`)
  - `api_audio/devices`: gestisce `ImportError` sounddevice, restituisce lista vuota su VPS headless invece di 500
  - `api_cues`: espone `scene_id` e `scene_name` (rimosso `act_id`)
  - `import re` aggiunto
- `client-regia/intercom-board.html`: fix VU meter microfono regia — usa `getUserMedia` indipendente invece del track LiveKit mutato

### Fix
- `POST /api/script/convert` → 400: loader nativo per nuovo formato JSON
- `GET /api/script/download` → 404: endpoint aggiunto
- `POST /api/tts/pregenerate` → 404: endpoint aggiunto
- `POST /api/tts/config` → 404: endpoint aggiunto
- `GET /api/audio/devices` → 500: gestione graceful su VPS senza sounddevice
- VU meter regia non funzionante dopo LiveKit mute: fix con stream getUserMedia separato
