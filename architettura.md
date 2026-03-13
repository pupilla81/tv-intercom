# Architettura del Sistema — TV Intercom

> Documento di riferimento. Aggiornare ad ogni decisione strutturale rilevante.
> Ultima revisione: 2025

---

## Punti Fermi (non modificabili senza consenso esplicito)

1. **Discord sempre attivo** come layer di comunicazione umana regia↔operatori e come failover
2. **Rete mobile individuale** — ogni dispositivo usa la propria SIM 4G/5G, nessuna dipendenza da WiFi locale
3. **Modularità** — ogni componente ha interfacce stabili e può essere sostituito indipendentemente
4. **Operatività senza mani** per gli operatori (PTT Bluetooth, replay con tasto secondario)
5. **Supervisione manuale sempre possibile** — la regia può scattare cue a mano e sovrascrivere il sistema automatico in qualsiasi momento

---

## Scenario Operativo

- **Evento:** rappresentazione teatrale in esterna, più location distanti tra loro
- **Troupe:** 1 regia + 5 operatori camera
- **Connettività:** SIM dati individuali su ogni dispositivo (no WiFi condiviso)
- **Copione:** pre-caricato nel sistema con battute-trigger e istruzioni per ogni camera

---

## Architettura Generale

```
MIXER AUDIO (feed pulito dal palco)
      │
      ▼
┌─────────────────────────────────────────────────────┐
│                    VPS CLOUD                        │
│                                                     │
│  [1] STT ENGINE       Whisper / Deepgram            │
│         │                                           │
│  [2] SCRIPT TRACKER   fuzzy matching copione        │
│         │                                           │
│  [3] CUE ENGINE       gestione istruzioni + TTS     │
│         │                                           │
│  [4] DISPATCHER       invio parallelo ai canali     │
│                                                     │
└──────────────┬──────────────────────────────────────┘
               │  WebRTC (LiveKit)
               │
    ┌──────────┼──────────┐
    ▼          ▼          ▼
[CAM 1 📱] [CAM 2 📱] [CAM 3-5 📱]
 istruzioni  istruzioni  istruzioni
 auto        auto        auto
    +          +          +
[Discord]  [Discord]  [Discord]
 voce regia  voce regia  voce regia
 PTT fisico  PTT fisico  PTT fisico
```

---

## Layer di Comunicazione

Il sistema opera su **due layer indipendenti e simultanei**:

| Layer | Strumento | Scopo | Failover |
|---|---|---|---|
| **Automatico** | WebRTC custom su VPS | Istruzioni copione → camere | Se cade: regia dà istruzioni manualmente via Discord |
| **Umano** | Discord | Comunicazione bidirezionale regia↔operatori | Sempre attivo, non dipende dal sistema custom |

I due layer sono **sempre attivi in parallelo**. Discord non è un failover da attivare ma un canale permanente.

---

## Componenti del Sistema

### [1] STT Engine — Speech to Text in tempo reale
- **Input:** audio dal mixer via scheda audio USB sul laptop di regia
- **Output:** trascrizione testuale continua con timestamp
- **Strumento primario:** OpenAI Whisper `large-v3` (locale, no costi per chiamata)
- **Alternativa cloud:** Deepgram (latenza ~300ms, migliore per real-time)
- **Sfide:** reverb teatrale, microfoni multipli, musica di scena → mitigato dal feed pulito del mixer

### [2] Script Tracker — Posizione nel copione
- **Input:** trascrizione STT + copione pre-processato
- **Output:** cue_id del prossimo cue da scattare + confidenza
- **Logica:** finestra scorrevole in avanti (non può tornare indietro nel copione)
- **Matching:** `rapidfuzz` per similarità testuale, soglia configurabile (default 80%)
- **Anticipo:** i cue possono essere configurati per scattare N secondi prima della battuta trigger

### [3] Cue Engine — Gestione istruzioni
- **Input:** cue_id attivato
- **Output:** lista di `{camera_id, audio_bytes}` da inviare in parallelo
- **TTS:** ElevenLabs con voce clonata della regia (opzione A) o file pre-registrati (opzione B, consigliata)
- **Pre-registrazione:** la regia registra tutte le istruzioni prima dell'evento → zero latenza TTS a runtime

### [4] Dispatcher — Invio simultaneo
- **Input:** lista `{camera_id, audio_bytes}`
- **Output:** stream audio inviati in parallelo sui canali WebRTC
- **Tecnologia:** LiveKit (open source, self-hosted sul VPS)
- **Garanzia:** invio parallelo, non sequenziale — cam1 e cam2 ricevono simultaneamente
- **Replay:** ultimo messaggio per canale conservato in memoria → richiesta dal client operatore

---

## Infrastruttura

### VPS Cloud
- **Provider consigliato:** Hetzner CX21 (~4€/mese) o DigitalOcean Droplet Basic (~6€/mese)
- **OS:** Ubuntu 22.04 LTS
- **Requisiti minimi:** 2 vCPU, 4GB RAM (Whisper locale richiede CPU significativa — valutare Deepgram se le performance non bastano)
- **Banda stimata:** ~100 kbps per operatore in ricezione = ~600 kbps totali, ampiamente gestibile

### Connettività operatori
- Ogni smartphone usa la propria SIM 4G/5G
- La web app si riconnette automaticamente in caso di perdita del segnale
- Discord gestisce autonomamente la riconnessione

### Hardware sul campo
- **Laptop regia:** connesso al mixer via scheda audio USB, gira STT + pannello regia
- **Smartphone operatori:** browser web (Chrome/Safari) per app custom + Discord
- **PTT Bluetooth:** pulsante selfie/presenter ~15€, mappato su Discord PTT

---

## Requisiti di Latenza

| Fase | Latenza stimata |
|---|---|
| Audio → STT (Whisper locale) | 1.0–2.0s |
| STT → Script Tracker | <100ms |
| Cue Engine → Dispatcher | <100ms |
| Dispatcher → Operatore (WebRTC) | 100–300ms |
| **Totale end-to-end** | **~1.5–2.5s** |

Accettabile se i cue nel copione sono posizionati con anticipo rispetto al momento in cui l'operatore deve eseguire.

---

## Gestione Errori e Failover

| Scenario | Risposta del sistema |
|---|---|
| STT non riconosce la battuta | Regia scatta cue manualmente dal pannello |
| Operatore perde segnale | App si riconnette automaticamente; Discord come canale di riserva |
| VPS non raggiungibile | Comunicazione solo via Discord; regia dà istruzioni manualmente |
| Cue scattato per errore | Pulsante "ANNULLA ULTIMO CUE" sul pannello regia |
| Operatore non ha sentito | Pulsante replay (tasto secondario Bluetooth) |

---

## Decisioni Aperte

| Decisione | Opzioni | Stato |
|---|---|---|
| TTS vs pre-registrato | ElevenLabs / File WAV pre-registrati | **Da decidere** |
| STT locale vs cloud | Whisper / Deepgram | Da valutare su hardware reale |
| WebRTC vs Discord per automatico | LiveKit / Discord Bot | LiveKit preferito, Discord come fallback |
| Formato copione | JSON / CSV con editor web | JSON deciso, editor TBD |
