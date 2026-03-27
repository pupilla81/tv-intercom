## [0.12.0] - 2026-03-28 — Dashboard SETTINGS, STT live nel Prompter, fix conference e latch

### Aggiunto
- **Tab SETTINGS** nella dashboard con layout a due colonne:
  - Link PWA operatore CAM 1-5 (voce+PTT) con pulsanti copia e apertura diretta
  - Link PWA istruzioni (sola ricezione) per ogni camera
  - TTS configurazione spostata qui dalla HOME (voce, modello, stability, similarity, style, test, rigenera)
  - STT parametri: engine selector + slider soglia fuzzy match (50-95%)
  - Link utili: Pannello Intercom, API Docs, uptime server
- **STT live box nel tab Prompter** — sopra la cue queue:
  - LED verde quando STT attivo, grigio quando inattivo
  - Testo trascrizione in tempo reale da STT CLI e STT browser
  - Aggiornato da WS `stt_transcript` (CLI) e direttamente dal browser STT
- **SKIP cue** su tutte le card nel prompter e nella cue queue destra:
  - `POST /api/cues/skip` — marca cue come fired senza inviare audio alle camere
  - Avanza il puntatore engine, notifica regia con `cue_skipped`
- **Overlay "Entra in regia"** nell'intercom-board:
  - Slider volume cuffie con valore iniziale 80%
  - Pulsante test audio TTS
  - VU meter microfono in tempo reale
  - Nota Firefox per Android
- **Master volume + Master mute** nella mode-bar dell'intercom-board:
  - Slider master volume — scala proporzionalmente il volume di tutte le camere
  - Pulsante MASTER mute — muta/smuta tutto l'audio in arrivo
  - Volume camere proporzionale al master: `masterVolume * camVolumes[i]`
- **MediaSession ⏮/⏭** nella PWA operatore per PTT ON/OFF con schermo bloccato

### Modificato
- **HOME col3** semplificata — rimangono solo i cue manuali (TTS config spostata in SETTINGS)
- **cue_engine.py**: normalizzazione accenti con `unicodedata` (stdlib):
  - `mostrerà` = `mostrera` per il fuzzy match — robusto alle trascrizioni STT senza accenti
  - lookahead aumentato da 2 a 4 (finestra di ricerca da 3 a 5 cue)
  - window_max aumentato da 5 a 6
  - pointer avanza correttamente anche per match fuori posizione 0
- **api_stt_chunk**: notifica `stt_transcript` ai director ad ogni chunk ricevuto
- **api_stt_stop**: aggiorna sempre `stt_active=False` indipendentemente da `source`
- **Conference token identity**: `cam{N}-conf` invece di `cam{N}` — elimina conflitti con room individuale
- **intercom-board startMicMeter**: usa `getUserMedia` indipendente da LiveKit — VU meter funziona su tutte le camere, non solo CAM1

### Fix
- **Conference + latch PWA operatore**: `pttTap` in latch ora gestisce correttamente `setConfMic()` — il mic conference si apre/chiude con il latch
- **Conference mic aperto su join**: sostituito `publishTrack+mute` con `setMicrophoneEnabled(true/false)` — mic parte sempre muto
- **Conference URL**: `lkUrl` salvato come variabile globale al momento della connessione, usato in `joinConference` invece di `lkRoom.url` (non esiste)
- **Latch timing bug**: `pttTap` non aveva più logica timing (causa: `isDouble` sempre true su tap normali). Doppio tap per uscire dal latch ora gestito in `touchstart` con variabile separata `lastLatchTap`
- **LED STT header**: `api_stt_stop` aggiornava `stt_active` solo per source!=browser — il poll restituiva sempre `true`. Fix: aggiornamento sempre eseguito
- **LED STT prompter**: non si spegneva allo stop. Fix in `sttStopAuto` e `updateUI`
- **VU meter intercom-board**: funzionava solo su CAM1 perché `startMicMeter(track)` dipendeva dal track LiveKit. Fix: `getUserMedia` indipendente, guard `if (vuInterval) return`
- **Conference camere 3-5 non comunicavano**: identity `cam{N}` in conflitto con room individuale — fix con `cam{N}-conf`
- **Scroll alla prima riga su fire cue**: `markFired` aggiorna solo elementi DOM specifici senza full re-render
- **Auto-scroll prompter**: `setTimeout(50ms)` dopo reflow prima di `scrollIntoView`

### Leggibilità dashboard
- `--muted` da `#3d5060` a `#6a8a9a` — molto più leggibile su sfondo scuro
- Font base `13px`, dialogo prompter `16px`, line-height `2.0`
- Bottoni più grandi con `min-height: 28px` touch-friendly
- Tooltip `title` su tutti i pulsanti principali
