"""
cue_engine.py
-------------
Cuore del sistema automatico.
Riceve trascrizioni STT in ingresso, le confronta con le battute-trigger
del copione e scatta i cue quando la similarità supera la soglia.

Interfaccia pubblica:
    engine = CueEngine(cues)
    fired = engine.process(transcription_chunk)
    # fired -> lista di Cue scattati, da passare al Dispatcher
"""

from dataclasses import dataclass
from typing import Callable, Optional
from rapidfuzz import fuzz
from script_parser import Cue, load_script, get_auto_cues


@dataclass
class FiredCue:
    """Un cue che il CueEngine ha deciso di scattare."""
    cue: Cue
    matched_text: str
    confidence: float


class CueEngine:
    """
    Tiene traccia della posizione nel copione e scatta i cue
    al momento giusto.

    Principi:
    - Il puntatore avanza solo in avanti (no backtrack)
    - Un cue già scattato non può scattare di nuovo
    - Il cue corrente ha priorità; si controlla anche il successivo
      per gestire cue molto ravvicinati
    """

    def __init__(
        self,
        cues: list[Cue],
        on_cue_fired: Optional[Callable[[FiredCue], None]] = None,
        lookahead: int = 2,
    ):
        """
        cues:          lista ordinata di cue (solo automatici)
        on_cue_fired:  callback opzionale chiamata ogni volta che un cue scatta
        lookahead:     quanti cue futuri controllare oltre al corrente
        """
        self.cues = cues
        self.on_cue_fired = on_cue_fired
        self.lookahead = lookahead
        self.pointer = 0           # indice del prossimo cue da scattare
        self._history: list[FiredCue] = []

    @property
    def current_cue(self) -> Optional[Cue]:
        if self.pointer < len(self.cues):
            return self.cues[self.pointer]
        return None

    @property
    def is_finished(self) -> bool:
        return self.pointer >= len(self.cues)

    def process(self, text: str) -> list[FiredCue]:
        """
        Confronta `text` (chunk di trascrizione STT) con i prossimi
        cue nel copione. Ritorna la lista dei cue scattati (spesso 0 o 1,
        raramente più di uno).
        """
        if self.is_finished or not text.strip():
            return []

        fired: list[FiredCue] = []
        # Controlla il cue corrente + lookahead cue futuri
        window = self.cues[self.pointer : self.pointer + 1 + self.lookahead]

        for i, cue in enumerate(window):
            if cue.fired:
                continue

            score = self._match(text, cue.trigger.text)

            if score >= cue.trigger.match_threshold:
                cue.fired = True
                fc = FiredCue(cue=cue, matched_text=text, confidence=score)
                fired.append(fc)
                self._history.append(fc)

                # Avanza il puntatore se questo era il cue corrente
                if i == 0:
                    self.pointer += 1

                if self.on_cue_fired:
                    self.on_cue_fired(fc)

        return fired

    def force_fire(self, cue_id: str) -> Optional[FiredCue]:
        """
        Scatta manualmente un cue per cue_id (chiamato dal pannello regia).
        Avanza il puntatore se necessario.
        """
        for i, cue in enumerate(self.cues):
            if cue.cue_id == cue_id and not cue.fired:
                cue.fired = True
                fc = FiredCue(cue=cue, matched_text="[MANUALE]", confidence=1.0)
                self._history.append(fc)
                if i >= self.pointer:
                    self.pointer = i + 1
                if self.on_cue_fired:
                    self.on_cue_fired(fc)
                return fc
        return None

    def reset(self):
        """Riporta il motore all'inizio (utile per prove)."""
        for c in self.cues:
            c.fired = False
        self.pointer = 0
        self._history.clear()

    @staticmethod
    def _match(transcription: str, trigger_text: str) -> float:
        """
        Confronto fuzzy tra la trascrizione e la battuta-trigger.
        Usa token_set_ratio per essere robusto a parole mancanti/aggiunte.
        Ritorna un valore 0.0–1.0.
        """
        score = fuzz.token_set_ratio(
            transcription.lower().strip(),
            trigger_text.lower().strip()
        )
        return score / 100.0

    def status(self) -> str:
        remaining = len(self.cues) - self.pointer
        fired_count = sum(1 for c in self.cues if c.fired)
        next_cue = self.current_cue
        next_str = (
            f'"{next_cue.trigger.text[:40]}..."'
            if next_cue and next_cue.trigger.text
            else "—"
        )
        return (
            f"CueEngine | {fired_count}/{len(self.cues)} scattati | "
            f"Prossimo: [{next_str}] | Rimanenti: {remaining}"
        )


# ---------------------------------------------------------------------------
# Test / demo standalone
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from script_parser import load_script, get_auto_cues

    meta, all_cues = load_script("sample_script.json")
    auto_cues = get_auto_cues(all_cues)

    def on_fired(fc: FiredCue):
        print(f"\n  🔔 CUE SCATTATO: {fc.cue.cue_id} (confidenza: {fc.confidence:.0%})")
        for instr in fc.cue.instructions:
            print(f"     📷 CAM {instr.camera}: {instr.text}")

    engine = CueEngine(auto_cues, on_cue_fired=on_fired)

    print("=" * 60)
    print(f"Test CueEngine — {meta['title']}")
    print(f"Cue automatici caricati: {len(auto_cues)}")
    print("=" * 60)

    # Simuliamo trascrizioni STT con variazioni realistiche (parole mancanti, errori)
    test_transcriptions = [
        "allora stasera siamo qui riuniti",
        "sono tornato finalmente sono tornato a casa",          # CUE01 atteso
        "dopo tutto questo tempo non ci credevo",
        "non ho paura del buio ho paura di quello che c'è dentro",  # CUE02 atteso (variazione)
        "tutto sembra diverso da come lo ricordavo",
        "ti aspettavo da tanto credevo non saresti mai venuto",  # CUE03 atteso (variazione)
    ]

    for chunk in test_transcriptions:
        print(f"\n  STT: \"{chunk}\"")
        print(f"  {engine.status()}")
        engine.process(chunk)

    print("\n" + "=" * 60)
    print("Test completato.")
