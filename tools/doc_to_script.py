"""
tools/doc_to_script.py
----------------------
Converte testo libero copione → script.json

Formato supportato:
    TITOLO COPIONE              ← prima riga non vuota = titolo (se --title non specificato)

    SCENA 1 - Titolo scena     ← intestazione scena (opzionale ATTO N ignorato)

    [MANUALE: nota regia]      ← cue manuale di regia
    [CAM1: istruzione]         ← parte del blocco manuale se segue [MANUALE:]
    [CAM2: istruzione]

    PERSONAGGIO                ← nome attore (riga maiuscola) = trigger STT
    Testo della battuta...     ← testo che STT ascolta
    [CAM1: istruzione]         ← cue automatico legato alla battuta sopra
    [CAM2: istruzione]

    SCENA 2 - Altro titolo
    ...

Regole:
    - ATTO N → ignorato (trattato come separatore visivo)
    - Prima riga non vuota → titolo se --title non specificato
    - Blocchi [CAMx:] dopo testo personaggio → cue automatici (trigger STT)
    - Blocchi [MANUALE:] + [CAMx:] seguenti → cue manuale
    - Testo senza brackets → battute / narratore (appare nel prompter)

Uso:
    python doc_to_script.py --input copione.txt --title "Titolo"
    python doc_to_script.py --input copione.txt --output script.json
    python doc_to_script.py --interactive
"""

import re
import json
import argparse
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Regex
# ---------------------------------------------------------------------------
RE_SCENE   = re.compile(r'^SCENA\s+(\d+)\s*(?:-\s*(.+))?$', re.IGNORECASE)
RE_ACT     = re.compile(r'^ATTO\s+\d+', re.IGNORECASE)
RE_CAM     = re.compile(r'^\[CAM(\d+):\s*(.+)\]$', re.IGNORECASE)
RE_MANUAL  = re.compile(r'^\[MANUALE:\s*(.+)\]$', re.IGNORECASE)
RE_CHAR    = re.compile(r'^[A-ZÀÈÉÌÒÙÁÉÍÓÚ][A-ZÀÈÉÌÒÙÁÉÍÓÚ\s]{1,30}$')


def is_character(line: str) -> bool:
    """Riga di personaggio: tutto maiuscolo, breve, nessun bracket."""
    return bool(RE_CHAR.match(line.strip())) and '[' not in line


def parse_script(text: str, title: str = "", date: str = "", location: str = "") -> dict:
    lines = text.splitlines()
    cues = []
    script_lines_out = []   # per il prompter: lista di blocchi testo/cue in ordine

    # --- Auto-titolo dalla prima riga non vuota ---
    if not title:
        for line in lines:
            stripped = line.strip()
            if stripped and not RE_SCENE.match(stripped) and not RE_ACT.match(stripped):
                title = stripped
                break

    current_scene_id   = 1
    current_scene_name = ""
    cue_counter        = 0

    # Stato parser
    pending_trigger_text = None   # testo STT in attesa di [CAMx:]
    pending_char         = None   # nome personaggio corrente
    in_manual_block      = False  # siamo dentro un blocco [MANUALE:]
    manual_desc          = ""
    manual_cams          = []     # {cam, text}

    def flush_manual():
        nonlocal in_manual_block, manual_desc, manual_cams, cue_counter
        if not manual_cams:
            in_manual_block = False
            return
        cue_counter += 1
        cue_id = f"S{current_scene_id}-M{cue_counter:02d}"
        cues.append({
            "cue_id": cue_id,
            "scene_id": current_scene_id,
            "scene_name": current_scene_name,
            "trigger": {"type": "manual", "text": manual_desc, "match_threshold": None},
            "instructions": [{"camera": c["cam"], "text": c["text"]} for c in manual_cams],
            "fired": False,
        })
        script_lines_out.append({"type": "cue_ref", "cue_id": cue_id})
        in_manual_block = False
        manual_desc     = ""
        manual_cams     = []

    def flush_auto():
        nonlocal pending_trigger_text, pending_char, cue_counter
        # non fare nulla se non ci sono cam raccolte — il testo è solo narratore
        pending_trigger_text = None
        pending_char         = None

    # Raccoglitore cam per cue automatico corrente
    auto_cams = []

    def commit_auto():
        nonlocal pending_trigger_text, pending_char, cue_counter, auto_cams
        if pending_trigger_text and auto_cams:
            cue_counter += 1
            cue_id = f"S{current_scene_id}-A{cue_counter:02d}"
            cues.append({
                "cue_id": cue_id,
                "scene_id": current_scene_id,
                "scene_name": current_scene_name,
                "trigger": {"type": "line", "text": pending_trigger_text, "match_threshold": 0.75},
                "instructions": [{"camera": c["cam"], "text": c["text"]} for c in auto_cams],
                "fired": False,
            })
            script_lines_out.append({"type": "cue_ref", "cue_id": cue_id})
        pending_trigger_text = None
        pending_char         = None
        auto_cams            = []

    i = 0
    while i < len(lines):
        raw  = lines[i]
        line = raw.strip()
        i   += 1

        if not line:
            continue

        # --- ATTO → ignorato ---
        if RE_ACT.match(line):
            continue

        # --- SCENA ---
        m = RE_SCENE.match(line)
        if m:
            # Chiudi eventuali blocchi aperti
            commit_auto()
            flush_manual()
            current_scene_id   = int(m.group(1))
            current_scene_name = (m.group(2) or "").strip()
            script_lines_out.append({
                "type": "scene_header",
                "scene_id": current_scene_id,
                "scene_name": current_scene_name,
            })
            continue

        # --- [MANUALE:] ---
        m = RE_MANUAL.match(line)
        if m:
            commit_auto()
            flush_manual()
            in_manual_block = True
            manual_desc     = m.group(1).strip()
            manual_cams     = []
            script_lines_out.append({"type": "manual_desc", "text": manual_desc})
            continue

        # --- [CAMx:] ---
        m = RE_CAM.match(line)
        if m:
            cam_id   = int(m.group(1))
            cam_text = m.group(2).strip()
            if in_manual_block:
                manual_cams.append({"cam": cam_id, "text": cam_text})
            else:
                # Cue automatico: collegato al testo trigger corrente
                auto_cams.append({"cam": cam_id, "text": cam_text})
            continue

        # --- Qualsiasi altra riga: chiude blocchi cam aperti ---
        # Prima chiudi manuale se non è [CAMx:]
        if in_manual_block:
            flush_manual()

        # Chiudi eventuale cue automatico precedente
        if auto_cams:
            commit_auto()
        elif pending_trigger_text is not None:
            flush_auto()

        # --- Personaggio (tutto maiuscolo) ---
        if is_character(line):
            pending_char         = line
            pending_trigger_text = None
            auto_cams            = []
            script_lines_out.append({"type": "character", "text": line})
            continue

        # --- Testo dialogo/narratore ---
        # Se c'è un personaggio in attesa, questo è il suo testo trigger
        if pending_char and pending_trigger_text is None:
            pending_trigger_text = line
        elif pending_trigger_text:
            # Seconda riga di dialogo: commit del precedente, nuova riga trigger
            commit_auto()
            pending_trigger_text = line

        script_lines_out.append({"type": "dialogue", "text": line, "char": pending_char or ""})

    # Chiudi eventuali blocchi aperti a fine file
    if auto_cams:
        commit_auto()
    flush_manual()

    # Indice scene
    scenes = {}
    for cue in cues:
        sid = cue["scene_id"]
        if sid not in scenes:
            scenes[sid] = cue["scene_name"]

    return {
        "metadata": {
            "title":    title or "Copione",
            "date":     date,
            "location": location,
            "scenes":   [{"scene_id": k, "scene_name": v} for k, v in sorted(scenes.items())],
        },
        "script_lines": script_lines_out,
        "cues": cues,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Converti copione testo → JSON")
    parser.add_argument("--input",       "-i", help="File testo input")
    parser.add_argument("--output",      "-o", default="script.json", help="File JSON output")
    parser.add_argument("--title",       "-t", default="", help="Titolo (default: prima riga)")
    parser.add_argument("--date",        "-d", default="", help="Data evento")
    parser.add_argument("--location",    "-l", default="", help="Luogo")
    parser.add_argument("--interactive", action="store_true", help="Incolla testo interattivo")
    parser.add_argument("--preview",     action="store_true", help="Mostra riepilogo senza salvare")
    args = parser.parse_args()

    if args.interactive:
        print("Incolla il testo del copione, poi premi Ctrl+D (Linux/Mac) o Ctrl+Z (Windows):")
        text = sys.stdin.read()
    elif args.input:
        text = Path(args.input).read_text(encoding="utf-8")
    else:
        parser.print_help()
        sys.exit(1)

    script = parse_script(text, args.title, args.date, args.location)
    meta   = script["metadata"]
    cues   = script["cues"]
    auto   = [c for c in cues if c["trigger"]["type"] == "line"]
    manual = [c for c in cues if c["trigger"]["type"] == "manual"]

    print(f"\n{'='*50}")
    print(f"  {meta['title']}")
    print(f"{'='*50}")
    print(f"  Scene:   {len(meta['scenes'])}")
    print(f"  Cue tot: {len(cues)}  (auto: {len(auto)}, manuali: {len(manual)})")
    for s in meta["scenes"]:
        sc = [c for c in cues if c["scene_id"] == s["scene_id"]]
        print(f"  SCENA {s['scene_id']} — {s['scene_name'] or '—'}  ({len(sc)} cue)")

    if args.preview:
        print("\n[PREVIEW — file non salvato]")
        return

    out = Path(args.output)
    out.write_text(json.dumps(script, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n✓ Salvato in: {out.resolve()}")


if __name__ == "__main__":
    main()
