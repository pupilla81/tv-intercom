# Formato Copione e Cue — Specifiche

---

## Struttura del file copione

Il copione viene salvato come file JSON con questa struttura:

```
script.json
├── metadata         — informazioni sull'evento
├── acts[]           — atti dello spettacolo
│   └── scenes[]     — scene
│       └── cues[]   — singoli cue con battuta-trigger e istruzioni
```

---

## Schema completo

```json
{
  "metadata": {
    "title": "Titolo dello spettacolo",
    "date": "2025-06-15",
    "location": "Teatro XY, Milano",
    "cameras": [1, 2, 3, 4, 5],
    "version": "1.0"
  },

  "acts": [
    {
      "act_id": "ACT1",
      "title": "Atto Primo",
      "scenes": [
        {
          "scene_id": "ACT1_SC1",
          "title": "Scena 1 — L'arrivo",
          "cues": [

            {
              "cue_id": "ACT1_SC1_CUE01",
              "trigger": {
                "type": "line",
                "text": "Non ho paura del buio, ho paura di ciò che nasconde",
                "character": "MARCO",
                "match_threshold": 0.80,
                "advance_seconds": 2.0
              },
              "instructions": [
                {
                  "camera": 1,
                  "text": "Zoom in sul volto di Marco, f/2.8",
                  "audio_file": "audio/ACT1_SC1_CUE01_cam1.wav",
                  "priority": "normal"
                },
                {
                  "camera": 3,
                  "text": "Allarga sul palco completo, mantieni profondità di campo",
                  "audio_file": "audio/ACT1_SC1_CUE01_cam3.wav",
                  "priority": "normal"
                }
              ]
            },

            {
              "cue_id": "ACT1_SC1_CUE02",
              "trigger": {
                "type": "line",
                "text": "Luci! Qualcuno spenga quelle luci!",
                "character": "SARA",
                "match_threshold": 0.75,
                "advance_seconds": 0.5
              },
              "instructions": [
                {
                  "camera": 2,
                  "text": "Stai pronto, segui Sara verso il fondo",
                  "audio_file": "audio/ACT1_SC1_CUE02_cam2.wav",
                  "priority": "urgent"
                },
                {
                  "camera": 4,
                  "text": "Wide sul palco, cattura la reazione del pubblico",
                  "audio_file": "audio/ACT1_SC1_CUE02_cam4.wav",
                  "priority": "normal"
                },
                {
                  "camera": 5,
                  "text": "Standby, sei il prossimo",
                  "audio_file": "audio/ACT1_SC1_CUE02_cam5.wav",
                  "priority": "normal"
                }
              ]
            }

          ]
        }
      ]
    }
  ]
}
```

---

## Campi spiegati

### `trigger`

| Campo | Tipo | Descrizione |
|---|---|---|
| `type` | `"line"` \| `"manual"` | `line` = rilevamento automatico, `manual` = solo da pannello regia |
| `text` | string | Battuta che fa scattare il cue |
| `character` | string | Personaggio che pronuncia la battuta (opzionale, migliora il matching) |
| `match_threshold` | float 0–1 | Soglia di similarità per il riconoscimento (0.80 = 80%) |
| `advance_seconds` | float | Anticipo in secondi rispetto alla battuta (0 = in contemporanea) |

### `instructions[]`

| Campo | Tipo | Descrizione |
|---|---|---|
| `camera` | int | Numero camera destinataria (1–5) |
| `text` | string | Testo dell'istruzione (usato per TTS o display) |
| `audio_file` | string | Path del file WAV pre-registrato (opzionale) |
| `priority` | `"normal"` \| `"urgent"` | `urgent` interrompe un messaggio in corso |

---

## Tipi di trigger

### `type: "line"` — automatico
Il sistema rileva la battuta tramite STT e scatta automaticamente.

### `type: "manual"` — solo regia
Il cue non viene mai scattato automaticamente. Appare sul pannello regia come pulsante da premere a mano. Utile per cue dipendenti da fattori non verbali (musica, luci, movimenti).

---

## File audio

I file audio delle istruzioni vivono in `script-parser/audio/` con naming convention:

```
{cue_id}_cam{N}.wav
```

Esempio: `ACT1_SC1_CUE01_cam1.wav`

Se `audio_file` non è specificato, il sistema genera il TTS al runtime dalla stringa `text`.

**Raccomandazione:** pre-registrare tutti i file prima dell'evento per eliminare latenza TTS e garantire la voce della regia reale.

---

## Script di esempio completo

Vedi [`sample_script.json`](sample_script.json) per uno script di esempio con 2 atti, 4 scene e 8 cue.
