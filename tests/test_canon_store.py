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
