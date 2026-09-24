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
    ArcMap,
    Canon,
    PendingEpisode,
    CanonDelta,
    EpisodeRecord,
    GenreProfile,
    NorthStar,
    Summary,
    VoiceBible,
)
from .ledgers import ForeshadowLedger, RhythmState

# The canon as the human locked it — the only thing a reset can safely rewind to.
_AUTHORED = "canon.authored.json"


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
        # Optional: the console locks a premise before an arc plan exists, and
        # every store created before L2 shipped has none.
        arc_map: ArcMap | None = None,
    ) -> None:
        """Setup gate PASS: write the stable spine and create EMPTY, versioned
        ForeshadowLedger + RhythmState + Summary (invariant #4)."""
        self.root.mkdir(parents=True, exist_ok=True)
        self.episodes_dir.mkdir(exist_ok=True)
        self._write("genre_profile.json", genre_profile)
        self._write("north_star.json", north_star)
        self._write("voice_bible.json", voice_bible)
        self._write("canon.json", canon)
        # The reset needs something to rewind TO. Character status and knowledge
        # accrued from episodes cannot be told apart from the authored card
        # after the fact, so the authored shape is kept as its own file.
        self._write(_AUTHORED, canon)
        if arc_map is not None:
            self._write("arc_map.json", arc_map)
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
        # An author edit IS the setup — rewinding past it would silently undo
        # their correction. The Canonicalizer's writes never land here.
        if canon.last_modified_by == "author":
            self._write(_AUTHORED, canon)

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

    # ── the author's accept gate ──────────────────────────────────────────
    def pending_episode(self) -> PendingEpisode | None:
        """The episode waiting for the author, or None."""
        path = self.root / "pending.json"
        if not path.exists():
            return None
        return PendingEpisode.model_validate_json(path.read_text(encoding="utf-8"))

    def hold_episode(self, pending: PendingEpisode) -> None:
        self._write("pending.json", pending)

    def clear_pending(self) -> None:
        (self.root / "pending.json").unlink(missing_ok=True)

    def load_arc_map(self) -> ArcMap | None:
        """The serial's overall picture, or None if this store predates it.

        None rather than a raise: the typed loaders are bare read_text, and a
        missing file inside commit_episode_state would fire after the gate
        passed and after the ledgers were written — a half-committed episode.
        """
        path = self.root / "arc_map.json"
        if not path.exists():
            return None
        return ArcMap.model_validate_json(path.read_text(encoding="utf-8"))

    def save_arc_map(self, arc_map: ArcMap) -> None:
        self._write("arc_map.json", arc_map)

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

        # An author edit must not be silently reverted by the extractor
        # (tester feedback 2-③). Knowledge is still appended — that is additive
        # and safe — but the mutable status fields the author just corrected by
        # hand are left alone.
        author_owned = canon.last_modified_by == "author"

        for name, upd in delta.character_updates.items():
            card = canon.characters.get(name)
            if card is None:        # hallucinated name: drop, never auto-create
                continue
            if not author_owned:
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
            card = canon.characters.get(name)
            if card is None:
                continue
            seen = {f.fact for f in card.known_facts}
            card.known_facts += [f for f in facts if f.fact not in seen]  # append-only

        # dedupe against what canon already holds — a serial that re-appends the
        # same rule every episode bloats the cache-stable prefix without bound
        known_rules = {r.text for r in canon.world_rules}
        canon.world_rules += [r for r in delta.new_world_rules if r.text not in known_rules]
        known_terms = {g.canonical_form for g in canon.glossary}
        canon.glossary += [g for g in delta.new_glossary if g.canonical_form not in known_terms]

        # the writer is the extractor again; an author edit sets this back
        canon.last_modified_by = "llm"
        canon.version += 1

        self.save_canon(canon)
        return canon

    # ── episodes ───────────────────────────────────────────────────────────────
    def commit_episode(self, record: EpisodeRecord) -> None:
        """Commit an episode, archiving any version it replaces (클로드 제안 8).

        Re-drafting the same episode used to overwrite silently, so a worse
        retry destroyed a better take with no way back. Now the driver can retry
        freely and the author can compare — the previous version is kept before
        the new one lands.
        """
        self.episodes_dir.mkdir(parents=True, exist_ok=True)
        path = self.episodes_dir / f"{record.episode_number:04d}.json"
        if path.exists():
            archive = self.episodes_dir / "versions"
            archive.mkdir(exist_ok=True)
            prior = len(list(archive.glob(f"{record.episode_number:04d}.v*.json"))) + 1
            (archive / f"{record.episode_number:04d}.v{prior}.json").write_text(
                path.read_text(encoding="utf-8"), encoding="utf-8")
        path.write_text(record.model_dump_json(indent=2), encoding="utf-8")

    def reset_serial(self, *, keep_setup: bool = True) -> bool:
        """Rewind to the locked setup so the serial can be re-run from 1화.

        Deleting episodes/ ALONE is not a reset and is an easy mistake to make:
        canon keeps the facts the Canonicalizer extracted, the summary keeps
        "1화: …", and the ledgers keep their debt. The next run is then asked to
        write episode 1 of a story whose canon already contains that story's
        ending, so every draft contradicts canon and Track A blocks all of them.
        Measured — that is exactly what a partial reset produced.

        keep_setup=True preserves the human-locked artifacts (GenreProfile,
        NorthStar, Canon's *authored* shape, VoiceBible) but drops everything
        the serial accumulated. Character status/knowledge accrued from episodes
        cannot be separated from the authored card, so canon is restored from
        the snapshot taken at setup (refreshed by every author edit).

        Returns False when the store predates that snapshot and only a partial
        rewind was possible.
        """
        import shutil

        shutil.rmtree(self.episodes_dir, ignore_errors=True)
        self.clear_pending()
        self.save_summary(Summary())
        self.save_foreshadow(ForeshadowLedger())
        # Seeded from the profile the way initialize does. A bare RhythmState()
        # silently swapped the genre's pacing limits for the library defaults,
        # so pacing_directive fired on thresholds nobody asked for while the
        # same ContextPack printed the profile's real numbers.
        profile = self.load_genre_profile()
        self.save_rhythm(RhythmState(
            max_consecutive_frustration=profile.max_consecutive_frustration_beats,
            target_catharsis_cadence=profile.target_catharsis_cadence,
        ))

        # The plan is the author's setup and stays. Which of its threads the
        # serial got around to planting is accumulation, and rewinds.
        arc_map = self.load_arc_map()
        if arc_map is not None:
            for thread in arc_map.threads:
                thread.planted_as = ""
            self.save_arc_map(arc_map)

        snapshot = self.root / _AUTHORED
        full = snapshot.exists()
        if full:
            canon = Canon.model_validate_json(snapshot.read_text(encoding="utf-8"))
        else:
            # A store created before the snapshot existed. The extractor's
            # characters, rules and glossary cannot be told apart from the
            # authored ones here, so only the per-episode fields can go — the
            # caller is told the reset was partial rather than left to assume.
            canon = self.load_canon()
            for card in canon.characters.values():
                card.known_facts = []
                card.current_location = ""
                card.condition = ""
        canon.version = 0
        canon.last_modified_by = "author" if not keep_setup else canon.last_modified_by
        self._write("canon.json", canon)
        return full

    def episode_versions(self, n: int) -> list[EpisodeRecord]:
        """Superseded takes of episode n, oldest first. Empty if never redrafted."""
        archive = self.episodes_dir / "versions"
        if not archive.exists():
            return []
        return [
            EpisodeRecord.model_validate_json(p.read_text(encoding="utf-8"))
            for p in sorted(archive.glob(f"{n:04d}.v*.json"),
                            key=lambda p: int(p.stem.split(".v")[1]))
        ]

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
