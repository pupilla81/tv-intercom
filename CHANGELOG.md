## [0.11.0] - 2026-03-24 — Libreria copioni, SKIP cue, auto-scroll, STT tracker

### Aggiunto
- `main.py`: `POST /api/cues/skip` — marca cue come fired senza inviare audio alle camere, avanza il puntatore engine. Notifica regia con `cue_skipped`
- `main.py`: `DELETE /api/script/file?name=file.json` — elimina singolo file JSON dalla libreria senza toccare cache TTS degli altri copioni
- `main.py`: `GET /api/script/download?name=file.json` — scarica file specifico dalla libreria o copione attivo se name non specificato
- `dashboard.html`: libreria copioni con riga per ogni file — pulsanti `▶ CARICA`, `⬇ SCARICA`, `🗑 ELIMINA` inline. File attivo evidenziato in ambra
- `dashboard.html`: pulsanti `▶ FIRE` + `⏭ SKIP` su ogni card del prompter e su ogni riga della cue list destra
- `dashboard.html`: auto-scroll nel prompter al prossimo cue non scattato quando scatta un cue (via WS `cue_fired` o click manuale), attivabile/disattivabile con bottone AUTO
- `tools/avvia_tracker.bat`: versione Windows con auto-reconnect (loop automatico su disconnessione, Ctrl+C per uscire)
- `tools/avvia_tracker.sh`: versione macOS equivalente con auto-reconnect

### Modificato
- `main.py`: `POST /api/script/load` — rileva automaticamente formato JSON nuovo (`cues` array) vs vecchio (`acts`), usa il loader corretto. Risolve errore "acts" al caricamento da libreria
- `dashboard.html`: `markFired` aggiorna solo elementi DOM specifici invece di full re-render — risolve scroll alla prima riga su fire cue
- `dashboard.html`: `updateNext` accetta parametro `doScroll=false` per evitare interferenze con auto-scroll del prompter
- `dashboard.html`: bottone AUTO ▼ funziona correttamente — verde=attivo, grigio=disattivato
- `dashboard.html`: WS handler gestisce `cue_skipped` identicamente a `cue_fired`

### Fix
- Scroll alla prima riga del copione quando si scattava una cue — causato da `renderCueList()` che ricostruiva il DOM e resettava lo scroll del prompter prima di `scrollIntoView`
- Auto-scroll non funzionava — ora usa `setTimeout(50ms)` per attendere fine reflow prima di eseguire lo scroll
- Caricamento JSON dalla libreria restituiva errore "acts" — formato non riconosciuto dal vecchio `load_script_file`
