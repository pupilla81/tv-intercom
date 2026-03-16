"""
tools/doc_to_script.py
----------------------
Convertitore da testo copione (Google Doc / testo semplice)
al formato JSON del sistema TV Intercom.

Convenzione nel documento:
    PERSONAGGIO
    Battuta del personaggio.
    [CAM1: istruzione per camera 1]
    [CAM3: istruzione per camera 3]

Utilizzo:
    python doc_to_script.py --input copione.txt --output script.json
    python doc_to_script.py --interactive --output script.json
    python doc_to_script.py --input copione.txt --title "Romeo e Giulietta" --date "2026-06-15"
"""

import argparse
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Configurazione defaults
# ---------------------------------------------------------------------------
DEFAULT_MATCH_THRESHOLD = 0.78
DEFAULT_ADVANCE_SECONDS = 1.5
CAMERAS = [1, 2, 3, 4, 5]

# Pattern
CAM_PATTERN    = re.compile(r'\[CAM\s*(\d+)\s*:\s*(.+?)\]', re.IGNORECASE)
ACT_PATTERN    = re.compile(r'^(ATTO|ACT)\s+', re.IGNORECASE)
SCENE_PATTERN  = re.compile(r'^(SCENA|SCENE)\s+', re.IGNORECASE)
MANUAL_PATTERN = re.compile(r'^\[(CUE|MANUALE)[:\s]', re.IGNORECASE)
CHAR_PATTERN   = re.compile(r'^[A-ZÀÈÌÒÙ][A-ZÀÈÌÒÙ\s]{1,30}$')


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------
def parse_script(text: str, title="", date="", location="") -> dict:
    lines = [l.rstrip() for l in text.splitlines()]

    # Struttura corrente
    act_num   = 0
    scene_num = 0
    cue_count = 0

    def act_id():   return f"ACT{act_num}"
    def scene_id(): return f"ACT{act_num}_SC{scene_num}"

    # Accumulatori
    acts   = {}   # act_id -> {"title": str, "scenes": {scene_id -> {"title", "cues"}}}
    
    def ensure_scene():
        a = act_id()
        s = scene_id()
        if a not in acts:
            acts[a] = {"title": f"Atto {act_num}", "scenes": {}}
        if s not in acts[a]["scenes"]:
            acts[a]["scenes"][s] = {"title": f"Scena {scene_num}", "cues": []}

    def add_cue(trigger_type, trigger_text, character, instructions):
        nonlocal cue_count
        ensure_scene()
        cue_count += 1
        cue_id = f"{scene_id()}_CUE{cue_count:02d}"
        cue = {
            "cue_id": cue_id,
            "trigger": {
                "type": trigger_type,
                "text": trigger_text,
                "character": character,
                "match_threshold": DEFAULT_MATCH_THRESHOLD if trigger_type == "line" else None,
                "advance_seconds": DEFAULT_ADVANCE_SECONDS if trigger_type == "line" else 0,
            },
            "instructions": [
                {
                    "camera": int(cam),
                    "text": instr.strip(),
                    "audio_file": None,
                    "priority": "normal"
                }
                for cam, instr in instructions
            ]
        }
        acts[act_id()]["scenes"][scene_id()]["cues"].append(cue)

    # Stato macchina
    current_char    = None
    current_line    = None
    pending_instrs  = []   # lista di (cam_num, testo)
    in_manual       = False

    def flush():
        """Emetti cue se ci sono istruzioni pendenti."""
        nonlocal in_manual
        if not pending_instrs:
            return
        if in_manual:
            add_cue("manual", None, None, pending_instrs)
            in_manual = False
        elif current_line:
            add_cue("line", current_line, current_char, pending_instrs)
        pending_instrs.clear()

    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            continue

        # --- ATTO ---
        if ACT_PATTERN.match(stripped):
            flush()
            act_num  += 1
            scene_num = 0
            cue_count = 0
            current_char = None
            current_line = None
            ensure_scene()
            # aggiorna titolo atto
            acts[act_id()]["title"] = stripped
            continue

        # --- SCENA ---
        if SCENE_PATTERN.match(stripped):
            flush()
            scene_num += 1
            cue_count  = 0
            current_char = None
            current_line = None
            ensure_scene()
            acts[act_id()]["scenes"][scene_id()]["title"] = stripped
            continue

        # --- CUE MANUALE [MANUALE: ...] o [CUE: ...] ---
        if MANUAL_PATTERN.match(stripped):
            flush()
            in_manual    = True
            current_line = None
            continue

        # --- ISTRUZIONE CAMERA [CAM1: ...] ---
        cam_matches = CAM_PATTERN.findall(stripped)
        if cam_matches:
            pending_instrs.extend(cam_matches)
            continue

        # --- PERSONAGGIO (riga tutta in maiuscolo) ---
        if CHAR_PATTERN.match(stripped):
            flush()
            current_char = stripped
            current_line = None
            in_manual    = False
            continue

        # --- BATTUTA ---
        # Considera solo se abbiamo un personaggio corrente
        if current_char:
            # Se c'erano istruzioni pendenti per la battuta precedente, emetti
            if pending_instrs and current_line:
                flush()
            current_line = stripped

    # Flush finale
    flush()

    # Costruisci JSON finale
    ensure_scene()
    acts_list = []
    for aid, act_data in acts.items():
        scenes_list = [
            {
                "scene_id": sid,
                "title": sdata["title"],
                "cues": sdata["cues"]
            }
            for sid, sdata in act_data["scenes"].items()
            if sdata["cues"]
        ]
        if scenes_list:
            acts_list.append({
                "act_id": aid,
                "title": act_data["title"],
                "scenes": scenes_list
            })

    return {
        "metadata": {
            "title": title or "Spettacolo",
            "date": date or "",
            "location": location or "",
            "cameras": CAMERAS,
            "version": "1.0"
        },
        "acts": acts_list
    }


# ---------------------------------------------------------------------------
# Sommario
# ---------------------------------------------------------------------------
def print_summary(script: dict):
    meta = script["metadata"]
    all_cues = [
        cue
        for act in script["acts"]
        for scene in act["scenes"]
        for cue in scene["cues"]
    ]
    auto   = sum(1 for c in all_cues if c["trigger"]["type"] == "line")
    manual = len(all_cues) - auto

    print(f"\n✅ Copione convertito con successo!")
    print(f"   Titolo:       {meta['title']}")
    print(f"   Data/Luogo:   {meta['date']} — {meta['location']}")
    print(f"   Atti:         {len(script['acts'])}")
    print(f"   Cue totali:   {len(all_cues)} ({auto} automatici, {manual} manuali)\n")

    for act in script["acts"]:
        print(f"  📖 [{act['act_id']}] {act['title']}")
        for scene in act["scenes"]:
            print(f"     🎬 [{scene['scene_id']}] {scene['title']} — {len(scene['cues'])} cue")
            for cue in scene["cues"]:
                cams = ", ".join(str(i["camera"]) for i in cue["instructions"])
                trigger = (
                    f'"{cue["trigger"]["text"][:50]}"'
                    if cue["trigger"]["text"]
                    else "[MANUALE]"
                )
                print(f"        [{cue['cue_id']}] CAM {cams} | {trigger}")
    print()


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="TV Intercom — Convertitore copione",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Formato nel documento:
  PERSONAGGIO
  Testo della battuta.
  [CAM1: istruzione camera 1]
  [CAM2: istruzione camera 2]

  ATTO 2
  SCENA 3 — Titolo scena

  [MANUALE: descrizione]
  [CAM4: istruzione manuale]

Esempi:
  python doc_to_script.py --input copione.txt --output script-parser/script.json
  python doc_to_script.py --input copione.txt --title "Romeo e Giulietta" --date "2026-06-15"
  python doc_to_script.py --interactive --output script-parser/script.json
        """
    )
    parser.add_argument("--input",       type=str, help="File testo copione")
    parser.add_argument("--output",      type=str, default="script.json")
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument("--title",       type=str, default="")
    parser.add_argument("--date",        type=str, default="")
    parser.add_argument("--location",    type=str, default="")
    parser.add_argument("--preview",     action="store_true", help="Stampa JSON senza salvare")
    args = parser.parse_args()

    if args.interactive:
        print("Incolla il copione (termina con una riga 'END'):")
        lines = []
        while True:
            try:
                line = input()
                if line.strip() == "END":
                    break
                lines.append(line)
            except EOFError:
                break
        text = "\n".join(lines)
    elif args.input:
        text = Path(args.input).read_text(encoding="utf-8")
    else:
        parser.print_help()
        sys.exit(1)

    script = parse_script(text, args.title, args.date, args.location)

    if args.preview:
        print(json.dumps(script, indent=2, ensure_ascii=False))
        return

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(script, indent=2, ensure_ascii=False), encoding="utf-8")
    print_summary(script)
    print(f"💾 Salvato in: {out}")


if __name__ == "__main__":
    main()
