"""File-based canon store (DESIGN §3, §5 "Store").

Phase 1a persistence = a directory of JSON files (the git-backed "novel repo").
No DB, no vector search until the canon outgrows the ContextPack budget.

Single-writer discipline (invariant #2): only the Canonicalizer applies a
CanonDelta. `apply_delta` is append-only for knowledge (known_facts / world
rules / glossary / episode history); current-STATUS fields (location, condition,
power_level, status) are mutable because a character genuinely moves/changes.
"""
from __future__ import annotations

import json
from pathlib import Path

from .artifacts import (
    Canon,
    CanonDelta,
    EpisodeRecord,
    GenreProfile,
    NorthStar,
    Summary,
    VoiceBible,
)
from .ledgers import ForeshadowLedger, RhythmState


class CanonStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.episodes_dir = self.root / "episodes"

    # ── setup ────────────────────────────────────────────────────────────────
    def initialize(
        self,
        *,
        genre_profile: GenreProfile,
        north_star: NorthStar,
        canon: Canon,
        voice_bible: VoiceBible,
    ) -> None:
        """Setup gate PASS: write the stable spine and create EMPTY, versioned
        ForeshadowLedger + RhythmState + Summary (invariant #4)."""
        self.root.mkdir(parents=True, exist_ok=True)
        self.episodes_dir.mkdir(exist_ok=True)
        self._write("genre_profile.json", genre_profile)
        self._write("north_star.json", north_star)
        self._write("voice_bible.json", voice_bible)
        self._write("canon.json", canon)
        self._write(
            "rhythm.json",
            RhythmState(
                max_consecutive_frustration=genre_profile.max_consecutive_frustration_beats,
                target_catharsis_cadence=genre_profile.target_catharsis_cadence,
            ),
        )
        self._write("foreshadow.json", ForeshadowLedger())
        self._write("summary.json", Summary())

    # ── typed load / save ─────────────────────────────────────────────────────
    def load_canon(self) -> Canon:
        return Canon.model_validate_json(self._read("canon.json"))

    def save_canon(self, canon: Canon) -> None:
        self._write("canon.json", canon)

    def load_foreshadow(self) -> ForeshadowLedger:
        return ForeshadowLedger.model_validate_json(self._read("foreshadow.json"))

    def save_foreshadow(self, ledger: ForeshadowLedger) -> None:
        self._write("foreshadow.json", ledger)

    def load_rhythm(self) -> RhythmState:
        return RhythmState.model_validate_json(self._read("rhythm.json"))

    def save_rhythm(self, rhythm: RhythmState) -> None:
        self._write("rhythm.json", rhythm)

    def load_summary(self) -> Summary:
        return Summary.model_validate_json(self._read("summary.json"))

    def save_summary(self, summary: Summary) -> None:
        self._write("summary.json", summary)

    def load_genre_profile(self) -> GenreProfile:
        return GenreProfile.model_validate_json(self._read("genre_profile.json"))

    def load_north_star(self) -> NorthStar:
        return NorthStar.model_validate_json(self._read("north_star.json"))

    def load_voice_bible(self) -> VoiceBible:
        return VoiceBible.model_validate_json(self._read("voice_bible.json"))

    # ── canon mutation (single writer) ─────────────────────────────────────────
    def apply_delta(self, delta: CanonDelta) -> Canon:
        """Append-only commit of one accepted episode's extracted state."""
        canon = self.load_canon()

        for name, card in delta.new_characters.items():
            canon.characters[name] = card

        for name, upd in delta.character_updates.items():
            card = canon.characters[name]
            if upd.status is not None:
                card.status = upd.status
            if upd.current_location is not None:
                card.current_location = upd.current_location
            if upd.condition is not None:
                card.condition = upd.condition
            if upd.power_level is not None:
                card.power_level = upd.power_level
            card.aliases += [a for a in upd.add_aliases if a not in card.aliases]
            card.relationships.update(upd.add_relationships)

        for name, facts in delta.new_known_facts.items():
            canon.characters[name].known_facts += facts  # append-only

        canon.world_rules += delta.new_world_rules
        canon.glossary += delta.new_glossary
        canon.version += 1

        self.save_canon(canon)
        return canon

    # ── episodes ───────────────────────────────────────────────────────────────
    def commit_episode(self, record: EpisodeRecord) -> None:
        self.episodes_dir.mkdir(parents=True, exist_ok=True)
        path = self.episodes_dir / f"{record.episode_number:04d}.json"
        path.write_text(record.model_dump_json(indent=2), encoding="utf-8")

    def load_episode(self, n: int) -> EpisodeRecord | None:
        path = self.episodes_dir / f"{n:04d}.json"
        if not path.exists():
            return None
        return EpisodeRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def latest_episode_number(self) -> int:
        if not self.episodes_dir.exists():
            return 0
        nums = [int(p.stem) for p in self.episodes_dir.glob("*.json")]
        return max(nums) if nums else 0

    # ── internals ──────────────────────────────────────────────────────────────
    def _write(self, name: str, model) -> None:
        (self.root / name).write_text(model.model_dump_json(indent=2), encoding="utf-8")

    def _read(self, name: str) -> str:
        return (self.root / name).read_text(encoding="utf-8")
