# 📡 TV Intercom — Sistema di Assistenza Regia

Sistema modulare per la comunicazione automatica e manuale tra regia e operatori camera durante eventi televisivi in esterna.

## Stato del progetto

| Modulo | Stato |
|---|---|
| `docs/` — Architettura e specifiche | ✅ Pronto |
| `script-parser/` — Parser copione + Cue Engine | ✅ Pronto |
| `stt-tracker/` — Ascolto audio + tracking copione | 🔲 Da sviluppare |
| `dispatcher/` — Invio istruzioni ai canali | 🔲 Da sviluppare |
| `server/` — Backend VPS | 🔲 Da sviluppare |
| `client-operator/` — App web operatore | 🔲 Da sviluppare |
| `client-regia/` — Pannello regia | ⚙️ Prototipo disponibile |

## Quick Start

```bash
# Installare dipendenze Python
pip install -r requirements.txt

# Testare il parser del copione
cd script-parser
python cue_engine.py
```

## Documentazione

- [`docs/architettura.md`](docs/architettura.md) — Decisioni architetturali fissate
- [`docs/cue-format.md`](docs/cue-format.md) — Formato del copione e dei cue
