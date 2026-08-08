"""Canonicalizer — the write-path step that makes this a serial engine (DESIGN §3).

Split deliberately into two halves:

  1. `commit_episode_state()` — DETERMINISTIC, no LLM. Advances the cross-episode
     ledgers (rhythm debt, foreshadow seeds), writes the EpisodeRecord, and bumps
     the summary. This half is pure and unit-testable.
  2. `extract_canon_delta()` — the LLM half that reads accepted prose and updates
     character state / world facts.

Why the split matters: the ledgers were previously loaded on every episode and
*never saved*, so pacing debt and foreshadow deadlines silently reset each time.
That half needs no model at all, so it should never have depended on one.

Single-writer discipline (invariant #2): this module is the only place that
mutates canon or the ledgers after setup.
"""
from __future__ import annotations

import hashlib

from .artifacts import BeatSheet, CanonDelta, Draft, EpisodeRecord, Summary
from .canon_store import CanonStore
from .ledgers import ForeshadowLedger, RhythmState


def episode_hash(prose: str) -> str:
    return hashlib.sha256(prose.encode("utf-8")).hexdigest()[:16]


def commit_episode_state(
    store: CanonStore,
    draft: Draft,
    beats: BeatSheet,
    *,
    human_edited: bool = False,
) -> EpisodeRecord:
    """Advance every cross-episode ledger and persist the episode.

    Called once per ACCEPTED episode. Deterministic: same inputs, same result.
    """
    # 1) rhythm — fold this episode's beat types into the running debt meter
    rhythm: RhythmState = store.load_rhythm()
    rhythm.record_episode(beats.beat_types())
    store.save_rhythm(rhythm)

    # 2) foreshadow — mint canonical ids for planted seeds, mark paid ones
    ledger: ForeshadowLedger = store.load_foreshadow()
    for planned in beats.seeds_to_plant:
        ledger.plant(planned, episode=beats.episode_number)
    for seed_id in beats.seeds_to_pay:
        if seed_id in ledger.seeds:
            ledger.pay(seed_id, episode=beats.episode_number)
    store.save_foreshadow(ledger)

    # 3) rolling summary — cheap deterministic accumulation (the LLM summarizer
    #    can replace this later; an empty summary was starving the next episode)
    summary: Summary = store.load_summary()
    line = f"{beats.episode_number}화: {beats.the_one_progression or beats.opening_hook}"
    summary.story_so_far = "\n".join(
        [s for s in (summary.story_so_far, line) if s]
    )[-4000:]
    summary.current_arc = beats.closing_cliffhanger or summary.current_arc
    store.save_summary(summary)

    # 4) the episode itself
    record = EpisodeRecord(
        episode_number=beats.episode_number,
        prose=draft.prose,
        accepted_draft_hash=episode_hash(draft.prose),
        human_edited=human_edited,
        beat_tags=beats.beat_types(),
    )
    store.commit_episode(record)
    return record


def apply_canon_delta(store: CanonStore, delta: CanonDelta) -> None:
    """Commit an extracted CanonDelta. Single writer; append-only for knowledge."""
    store.apply_delta(delta)
