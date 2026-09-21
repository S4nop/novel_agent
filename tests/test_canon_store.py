"""Behavior tests for the file-based canon store (DESIGN §3, §5)."""
from novel_agent.artifacts import (
    CanonDelta,
    CharacterUpdate,
    EpisodeRecord,
    KnownFact,
    WorldRule,
)
from novel_agent.canon_store import CanonStore

from .factories import canon, genre_profile, north_star, voice_bible


def _store(tmp_path) -> CanonStore:
    s = CanonStore(tmp_path / "novel")
    s.initialize(
        genre_profile=genre_profile(),
        north_star=north_star(),
        canon=canon(),
        voice_bible=voice_bible(),
    )
    return s


def test_setup_creates_empty_ledgers_and_summary(tmp_path):
    s = _store(tmp_path)
    assert s.load_foreshadow().seeds == {}
    assert s.load_rhythm().frustration_debt == 0
    assert s.load_summary().story_so_far == ""
    # rhythm config seeded from the genre profile
    assert s.load_rhythm().target_catharsis_cadence == genre_profile().target_catharsis_cadence


def test_canon_round_trips_through_store(tmp_path):
    s = _store(tmp_path)
    assert s.load_canon() == canon()


def test_apply_delta_appends_known_facts_without_mutating_existing(tmp_path):
    s = _store(tmp_path)
    s.apply_delta(CanonDelta(
        source_episode=1,
        new_known_facts={"김현우": [KnownFact(fact="회귀 사실을 숨긴다", learned_episode=1)]},
    ))
    s.apply_delta(CanonDelta(
        source_episode=2,
        new_known_facts={"김현우": [KnownFact(fact="길드장의 배신을 안다", learned_episode=2)]},
    ))
    facts = s.load_canon().characters["김현우"].known_facts
    assert [f.fact for f in facts] == ["회귀 사실을 숨긴다", "길드장의 배신을 안다"]  # append-only


def test_apply_delta_increments_canon_version(tmp_path):
    s = _store(tmp_path)
    assert s.load_canon().version == 0
    s.apply_delta(CanonDelta(source_episode=1, new_world_rules=[WorldRule(text="게이트는 밤에 열린다")]))
    assert s.load_canon().version == 1


def test_character_status_update_changes_mutable_field(tmp_path):
    s = _store(tmp_path)
    s.apply_delta(CanonDelta(
        source_episode=1,
        character_updates={"김현우": CharacterUpdate(current_location="2번 게이트", power_level="E급")},
    ))
    hero = s.load_canon().characters["김현우"]
    assert hero.current_location == "2번 게이트"
    assert hero.power_level == "E급"
    # immutable descriptors untouched
    assert hero.immutable_descriptors == ["검은 머리", "왼손 흉터"]


def test_episode_round_trips_and_reports_latest_number(tmp_path):
    s = _store(tmp_path)
    assert s.latest_episode_number() == 0
    assert s.load_episode(1) is None
    rec = EpisodeRecord(episode_number=1, prose="문이 열렸다.", accepted_draft_hash="abc123")
    s.commit_episode(rec)
    assert s.load_episode(1) == rec
    assert s.latest_episode_number() == 1


# ── episode versioning (클로드 제안 8) ────────────────────────────────────────
def test_redrafting_an_episode_archives_the_version_it_replaces(tmp_path):
    """Regression: re-drafting overwrote silently, so a worse retry destroyed a
    better take with no way back. The driver retries, so this is a live risk."""
    from novel_agent.artifacts import EpisodeRecord

    s = _store(tmp_path)
    s.commit_episode(EpisodeRecord(episode_number=1, prose="첫 번째 원고",
                                   accepted_draft_hash="a"))
    s.commit_episode(EpisodeRecord(episode_number=1, prose="두 번째 원고",
                                   accepted_draft_hash="b"))
    s.commit_episode(EpisodeRecord(episode_number=1, prose="세 번째 원고",
                                   accepted_draft_hash="c"))

    assert s.load_episode(1).prose == "세 번째 원고"          # newest is current
    assert [r.prose for r in s.episode_versions(1)] == ["첫 번째 원고", "두 번째 원고"]


def test_a_first_commit_archives_nothing(tmp_path):
    from novel_agent.artifacts import EpisodeRecord

    s = _store(tmp_path)
    s.commit_episode(EpisodeRecord(episode_number=1, prose="유일한 원고",
                                   accepted_draft_hash="a"))
    assert s.episode_versions(1) == []


def test_versions_are_kept_per_episode(tmp_path):
    from novel_agent.artifacts import EpisodeRecord

    s = _store(tmp_path)
    for n in (1, 2):
        s.commit_episode(EpisodeRecord(episode_number=n, prose=f"{n}화 v1",
                                       accepted_draft_hash="a"))
        s.commit_episode(EpisodeRecord(episode_number=n, prose=f"{n}화 v2",
                                       accepted_draft_hash="b"))
    assert [r.prose for r in s.episode_versions(1)] == ["1화 v1"]
    assert [r.prose for r in s.episode_versions(2)] == ["2화 v1"]


def test_archived_versions_do_not_confuse_the_resume_point(tmp_path):
    """latest_episode_number() does int(p.stem) — an archived '0001.v1.json'
    landing beside the current files would raise ValueError and break resume."""
    from novel_agent.artifacts import EpisodeRecord

    s = _store(tmp_path)
    for _ in range(3):
        s.commit_episode(EpisodeRecord(episode_number=1, prose="p", accepted_draft_hash="a"))
    s.commit_episode(EpisodeRecord(episode_number=2, prose="p", accepted_draft_hash="a"))
    assert s.latest_episode_number() == 2        # not confused by versions/


# ── resetting a serial (footgun found by a live run) ─────────────────────────
def test_reset_rewinds_everything_the_serial_accumulated(tmp_path):
    """Deleting episodes/ alone is NOT a reset: canon keeps the extracted facts
    and the summary keeps "1화: …", so the next run writes episode 1 against a
    canon that already holds that story's ending and Track A blocks every draft.
    Measured on a live run — three attempts, all continuity-blocked."""
    from novel_agent.artifacts import CanonDelta, EpisodeRecord, KnownFact, PlannedSeed

    s = _store(tmp_path)
    s.commit_episode(EpisodeRecord(episode_number=1, prose="p", accepted_draft_hash="a"))
    s.apply_delta(CanonDelta(source_episode=1, new_known_facts={
        "홍길동": [KnownFact(fact="정체가 드러났다", learned_episode=1)]}))
    summary = s.load_summary(); summary.story_so_far = "1화: 무언가 일어남"
    s.save_summary(summary)
    ledger = s.load_foreshadow(); ledger.plant(PlannedSeed(proposed_seed_id="x",
                                   description="떡밥", due_by_ep=5), episode=1)
    s.save_foreshadow(ledger)

    s.reset_serial()

    assert s.latest_episode_number() == 0
    assert s.load_summary().story_so_far == ""
    assert s.load_foreshadow().seeds == {}
    assert s.load_rhythm().frustration_debt == 0
    canon = s.load_canon()
    assert canon.version == 0
    assert all(not c.known_facts for c in canon.characters.values())


def test_reset_keeps_the_human_locked_setup(tmp_path):
    """A reset must not cost the author their premise, voice, or cast — only the
    serial's accumulation."""
    s = _store(tmp_path)
    before_cast = sorted(s.load_canon().characters)
    before_premise = s.load_north_star().premise
    before_voice = s.load_voice_bible().spec

    s.reset_serial()

    assert sorted(s.load_canon().characters) == before_cast
    assert s.load_north_star().premise == before_premise
    assert s.load_voice_bible().spec == before_voice


def test_a_reset_store_can_start_a_serial_from_episode_one(tmp_path):
    from novel_agent.artifacts import EpisodeRecord

    s = _store(tmp_path)
    for n in (1, 2, 3):
        s.commit_episode(EpisodeRecord(episode_number=n, prose="p", accepted_draft_hash="a"))
    assert s.latest_episode_number() == 3
    s.reset_serial()
    assert s.latest_episode_number() == 0        # driver resumes from 1화


def test_reset_drops_the_cast_and_world_the_extractor_invented(tmp_path):
    """The reset cleared three status fields, so every character, world rule,
    glossary term, alias and relationship the Canonicalizer wrote survived it —
    the next run opened episode 1 with the final boss already in the cast and
    the protagonist marked dead. That is the exact footgun reset_serial exists
    to prevent, and it printed "연재 초기화" while doing it."""
    from novel_agent.artifacts import (
        CanonDelta, CharacterCard, GlossaryEntry, WorldRule,
    )

    s = _store(tmp_path)
    authored = sorted(s.load_canon().characters)
    s.apply_delta(CanonDelta(
        source_episode=30,
        new_characters={"흑막": CharacterCard(name="흑막", is_main_cast=True,
                                              immutable_descriptors=["최종 보스"])},
        new_world_rules=[WorldRule(text="검왕은 죽지 않는다", hard=True)],
        new_glossary=[GlossaryEntry(term="천마신공", canonical_form="천마신공")],
    ))
    before = s.load_canon()
    assert "흑막" in before.characters                       # the delta really landed
    assert "천마신공" in [g.term for g in before.glossary]
    authored_rules = len(before.world_rules) - 1

    s.reset_serial()

    canon = s.load_canon()
    assert sorted(canon.characters) == authored
    assert len(canon.world_rules) == authored_rules
    assert [g.term for g in canon.glossary if g.term == "천마신공"] == []


def test_reset_restores_a_character_the_serial_killed_off(tmp_path):
    from novel_agent.artifacts import CanonDelta, CharacterUpdate

    s = _store(tmp_path)
    who = sorted(s.load_canon().characters)[0]
    before = s.load_canon().characters[who].model_copy(deep=True)
    s.apply_delta(CanonDelta(source_episode=30, character_updates={
        who: CharacterUpdate(status="사망", power_level="9성", add_aliases=["검왕"])}))
    assert s.load_canon().characters[who].status != before.status

    s.reset_serial()

    assert s.load_canon().characters[who] == before


def test_reset_keeps_a_canon_the_author_hand_edited(tmp_path):
    """The author's edit is the setup, not serial accumulation — rewinding to
    the model's first guess would silently undo their correction."""
    from novel_agent.artifacts import CanonDelta, CharacterUpdate

    s = _store(tmp_path)
    who = sorted(s.load_canon().characters)[0]
    edited = s.load_canon()
    edited.characters[who].immutable_descriptors = ["작가가 정한 특징"]
    edited.last_modified_by = "author"
    s.save_canon(edited)
    s.apply_delta(CanonDelta(source_episode=30, character_updates={
        who: CharacterUpdate(status="사망")}))

    s.reset_serial()

    assert s.load_canon().characters[who].immutable_descriptors == ["작가가 정한 특징"]


def test_reset_keeps_the_pacing_thresholds_the_genre_asked_for(tmp_path):
    """initialize seeds the rhythm from the GenreProfile; the reset wrote a bare
    RhythmState, so the run continued on library defaults (3/2) while the same
    ContextPack printed the profile's real numbers."""
    from .factories import canon, genre_profile, north_star, voice_bible

    s = CanonStore(tmp_path / "novel2")
    s.initialize(genre_profile=genre_profile(target_catharsis_cadence=6,
                                             max_consecutive_frustration_beats=4),
                 north_star=north_star(), canon=canon(), voice_bible=voice_bible())

    s.reset_serial()

    r = s.load_rhythm()
    assert (r.target_catharsis_cadence, r.max_consecutive_frustration) == (6, 4)
