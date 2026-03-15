"""
cue_engine.py
-------------
Cuore del sistema automatico.
Riceve trascrizioni STT in ingresso, le confronta con le battute-trigger
del copione e scatta i cue quando la similarita supera la soglia.

Usa una finestra scorrevole sugli ultimi chunk STT per gestire
le frasi spezzettate tra chunk diversi.

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
    - Un cue gia scattato non puo scattare di nuovo
    - Finestra scorrevole sugli ultimi N chunk per gestire
      frasi spezzettate tra chunk diversi
    """

    def __init__(
        self,
        cues: list[Cue],
        on_cue_fired: Optional[Callable[[FiredCue], None]] = None,
        lookahead: int = 2,
        window_max: int = 5,
    ):
        """
        cues:          lista ordinata di cue (solo automatici)
        on_cue_fired:  callback opzionale chiamata ogni volta che un cue scatta
        lookahead:     quanti cue futuri controllare oltre al corrente
        window_max:    quanti chunk STT tenere in memoria nella finestra scorrevole
        """
        self.cues = cues
        self.on_cue_fired = on_cue_fired
        self.lookahead = lookahead
        self.pointer = 0
        self._window_chunks: list[str] = []
        self._window_max: int = window_max
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
        Confronta il testo (chunk STT) con i prossimi cue nel copione.
        Usa una finestra scorrevole per gestire frasi spezzettate.
        Ritorna la lista dei cue scattati.
        """
        if self.is_finished or not text.strip():
            return []

        # Aggiorna la finestra scorrevole
        self._window_chunks.append(text.strip())
        if len(self._window_chunks) > self._window_max:
            self._window_chunks.pop(0)

        # Testa sia il chunk singolo che la finestra concatenata
        texts_to_test = [
            text,
            " ".join(self._window_chunks),
        ]

        fired: list[FiredCue] = []
        window = self.cues[self.pointer : self.pointer + 1 + self.lookahead]

        for i, cue in enumerate(window):
            if cue.fired:
                continue

            # Prendi il punteggio migliore tra chunk singolo e finestra
            best_score = max(
                self._match(t, cue.trigger.text) for t in texts_to_test
            )

            if best_score >= cue.trigger.match_threshold:
                cue.fired = True
                matched = " ".join(self._window_chunks)
                fc = FiredCue(cue=cue, matched_text=matched, confidence=best_score)
                fired.append(fc)
                self._history.append(fc)

                if i == 0:
                    self.pointer += 1

                # Reset finestra dopo un match per evitare falsi positivi
                self._window_chunks.clear()

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
        self._window_chunks.clear()
        self._history.clear()

    @staticmethod
    def _match(transcription: str, trigger_text: str) -> float:
        """
        Confronto fuzzy tra la trascrizione e la battuta-trigger.
        Usa token_set_ratio per essere robusto a parole mancanti/aggiunte.
        Ritorna un valore 0.0-1.0.
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
            else "-"
        )
        return (
            f"CueEngine | {fired_count}/{len(self.cues)} scattati | "
            f"Prossimo: [{next_str}] | Rimanenti: {remaining}"
        )


# ---------------------------------------------------------------------------
# Test / demo standalone
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    meta, all_cues = load_script("sample_script.json")
    auto_cues = get_auto_cues(all_cues)

    def on_fired(fc: FiredCue):
        print(f"\n  FIRED: {fc.cue.cue_id} (confidenza: {fc.confidence:.0%})")
        for instr in fc.cue.instructions:
            print(f"     CAM {instr.camera}: {instr.text}")

    engine = CueEngine(auto_cues, on_cue_fired=on_fired)

    print("=" * 60)
    print(f"Test CueEngine con finestra scorrevole")
    print(f"Cue automatici: {len(auto_cues)}")
    print("=" * 60)

    # Simula frasi spezzettate come farebbe lo STT reale
    test_chunks = [
        "ti aspettavo",
        "da tanto",
        "credevo che non",
        "saresti mai tornato",
    ]

    for chunk in test_chunks:
        print(f"\n  STT chunk: \"{chunk}\"")
        engine.process(chunk)

    print("\n" + "=" * 60)
    print("Test completato.")
