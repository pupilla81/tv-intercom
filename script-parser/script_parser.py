"""
script_parser.py
----------------
Carica e valida il copione JSON.
Restituisce una lista piatta di cue pronti per il CueEngine.
"""

import json
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path


@dataclass
class Instruction:
    camera: int
    text: str
    audio_file: Optional[str]
    priority: str  # "normal" | "urgent"


@dataclass
class Trigger:
    type: str               # "line" | "manual"
    text: Optional[str]
    character: Optional[str]
    match_threshold: Optional[float]
    advance_seconds: float


@dataclass
class Cue:
    cue_id: str
    act_id: str
    scene_id: str
    trigger: Trigger
    instructions: list[Instruction]
    # Runtime state
    fired: bool = field(default=False, repr=False)


def load_script(path: str) -> tuple[dict, list[Cue]]:
    """
    Carica il file JSON del copione.
    Ritorna (metadata, lista_cue) dove lista_cue è ordinata
    nell'ordine di apparizione nello spettacolo.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))

    metadata = data["metadata"]
    cues: list[Cue] = []

    for act in data["acts"]:
        for scene in act["scenes"]:
            for cue_data in scene["cues"]:
                t = cue_data["trigger"]
                trigger = Trigger(
                    type=t["type"],
                    text=t.get("text"),
                    character=t.get("character"),
                    match_threshold=t.get("match_threshold"),
                    advance_seconds=t.get("advance_seconds", 0.0),
                )
                instructions = [
                    Instruction(
                        camera=i["camera"],
                        text=i["text"],
                        audio_file=i.get("audio_file"),
                        priority=i.get("priority", "normal"),
                    )
                    for i in cue_data["instructions"]
                ]
                cues.append(Cue(
                    cue_id=cue_data["cue_id"],
                    act_id=act["act_id"],
                    scene_id=scene["scene_id"],
                    trigger=trigger,
                    instructions=instructions,
                ))

    return metadata, cues


def get_auto_cues(cues: list[Cue]) -> list[Cue]:
    """Restituisce solo i cue con trigger automatico (type='line')."""
    return [c for c in cues if c.trigger.type == "line"]


def get_manual_cues(cues: list[Cue]) -> list[Cue]:
    """Restituisce solo i cue manuali."""
    return [c for c in cues if c.trigger.type == "manual"]


def summary(metadata: dict, cues: list[Cue]) -> str:
    """Stampa un sommario leggibile del copione caricato."""
    auto = get_auto_cues(cues)
    manual = get_manual_cues(cues)
    cameras = set()
    for c in cues:
        for i in c.instructions:
            cameras.add(i.camera)

    lines = [
        f"📄 {metadata['title']}",
        f"   Data: {metadata['date']} — {metadata['location']}",
        f"   Camere: {sorted(cameras)}",
        f"   Cue totali: {len(cues)} ({len(auto)} automatici, {len(manual)} manuali)",
        "",
    ]
    for cue in cues:
        cam_list = [str(i.camera) for i in cue.instructions]
        trigger_str = (
            f'"{cue.trigger.text[:50]}..."'
            if cue.trigger.text
            else "[MANUALE]"
        )
        lines.append(
            f"  [{cue.cue_id}] → Cam {', '.join(cam_list)} | {trigger_str}"
        )

    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "sample_script.json"
    meta, cues = load_script(path)
    print(summary(meta, cues))
