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

from .artifacts import (
    BeatSheet,
    Canon,
    CanonDelta,
    CharacterCard,
    CharacterUpdate,
    Draft,
    EpisodeRecord,
    GlossaryEntry,
    KnownFact,
    Summary,
    WorldRule,
)
from .canon_store import CanonStore
from .ledgers import ForeshadowLedger, RhythmState
from .llm import LLM
from .prompt_store import render
from .schemas import CanonDeltaDraft


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


def _canon_digest(canon: Canon) -> str:
    """What the extractor already knows, so it reports only what CHANGED."""
    lines = []
    for name, c in canon.characters.items():
        bits = [f"- {name}"]
        if c.immutable_descriptors:
            bits.append("· " + ", ".join(c.immutable_descriptors))
        for label, value in (("상태", c.status), ("위치", c.current_location),
                             ("컨디션", c.condition), ("능력", c.power_level)):
            if value:
                bits.append(f"· {label}={value}")
        lines.append(" ".join(bits))
        for f in c.known_facts:
            lines.append(f"    · {f.fact}")
    return "\n".join([
        "[인물]", *(lines or ["- 없음"]),
        "", "[세계 규칙]", *([f"- {r.text}" for r in canon.world_rules] or ["- 없음"]),
        "", "[용어]", *([f"- {g.canonical_form}" for g in canon.glossary] or ["- 없음"]),
    ])


def extract_canon_delta(llm: LLM, draft: Draft, canon: Canon) -> CanonDelta:
    """The LLM half — read ACCEPTED prose and report what canon must now record.

    Called only on an accepted episode: extracting from a draft that failed the
    gate would write a contradiction into the source of truth.

    Deliberately conservative. Every mapping below drops what it cannot verify
    against the canon it was given, because a wrong canon fact is worse than a
    missing one — it propagates into every later episode's ContextPack and there
    is no read path that would ever catch it.
    """
    draft_delta = llm.structured(
        [
            {"role": "system", "content": render("canon_extract_system")},
            {"role": "user", "content": render(
                "canon_extract", canon=_canon_digest(canon),
                episode_number=draft.episode_number, prose=draft.prose)},
        ],
        CanonDeltaDraft,
    )

    known = set(canon.characters)
    delta = CanonDelta(source_episode=draft.episode_number)

    for c in draft_delta.new_characters:
        name = (c.name or "").strip()
        if name and name not in known:
            delta.new_characters[name] = CharacterCard(
                name=name, is_main_cast=c.is_main_cast,
                immutable_descriptors=[d for d in c.descriptors if d.strip()])
            known.add(name)

    for u in draft_delta.character_updates:
        name = (u.name or "").strip()
        if name not in known:          # never invent a card via an update
            continue
        upd = CharacterUpdate(
            status=u.status.strip() or None,
            current_location=u.current_location.strip() or None,
            condition=u.condition.strip() or None,
            power_level=u.power_level.strip() or None,
            add_aliases=[a.strip() for a in u.new_aliases if a.strip()],
        )
        if any((upd.status, upd.current_location, upd.condition,
                upd.power_level, upd.add_aliases)):
            delta.character_updates[name] = upd

    for f in draft_delta.new_known_facts:
        name, fact = (f.character or "").strip(), (f.fact or "").strip()
        if name in known and fact:
            delta.new_known_facts.setdefault(name, []).append(
                KnownFact(fact=fact, learned_episode=draft.episode_number))

    existing_rules = {r.text for r in canon.world_rules}
    delta.new_world_rules = [
        WorldRule(text=r.strip(), hard=True)
        for r in draft_delta.new_world_rules
        if r.strip() and r.strip() not in existing_rules
    ]
    existing_terms = {g.canonical_form for g in canon.glossary} | {g.term for g in canon.glossary}
    delta.new_glossary = [
        GlossaryEntry(term=t.strip(), canonical_form=t.strip())
        for t in draft_delta.new_glossary_terms
        if t.strip() and t.strip() not in existing_terms
    ]
    return delta


def apply_canon_delta(store: CanonStore, delta: CanonDelta) -> Canon:
    """Commit an extracted CanonDelta. Single writer; append-only for knowledge."""
    return store.apply_delta(delta)


def canonicalize_episode(llm: LLM, store: CanonStore, draft: Draft) -> CanonDelta:
    """Extract and commit in one step, for an episode that PASSED the gate.

    A model failure here must not lose the episode: the deterministic ledgers
    were already advanced by commit_episode_state, so a failed extraction costs
    canon accumulation for one episode, not the run.
    """
    try:
        delta = extract_canon_delta(llm, draft, store.load_canon())
    except Exception:  # noqa: BLE001 — advisory; the episode is already committed
        return CanonDelta(source_episode=draft.episode_number)
    apply_canon_delta(store, delta)
    return delta
