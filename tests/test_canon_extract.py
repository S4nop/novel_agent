"""The Canonicalizer's LLM half — 클로드 제안 1, the item that turns a pilot
generator into a serial engine.

Extraction runs on ACCEPTED prose only, and every mapping is deliberately
conservative: a wrong canon fact propagates into every later episode's
ContextPack and no read path would ever catch it, so dropping the unverifiable
is the correct bias.
"""
import pytest

from novel_agent.artifacts import (
    Canon,
    CanonDelta,
    CharacterCard,
    CharacterUpdate,
    Draft,
    GlossaryEntry,
    KnownFact,
    WorldRule,
)
from novel_agent.canon_store import CanonStore
from novel_agent.canonicalizer import (
    apply_canon_delta,
    canonicalize_episode,
    extract_canon_delta,
)
from novel_agent.schemas import (
    CanonDeltaDraft,
    CharacterUpdateDraft,
    KnownFactDraft,
    NewCharacterDraft,
)

from .factories import genre_profile, north_star, voice_bible


class StubExtractor:
    def __init__(self, draft=None, boom=False):
        self.draft, self.boom, self.prompts = draft or CanonDeltaDraft(), boom, []

    def structured(self, messages, schema):
        self.prompts.append(messages[-1]["content"])
        if self.boom:
            raise RuntimeError("rate limited")
        return self.draft

    def text(self, messages, *, max_tokens=8192):
        raise AssertionError("extraction must use structured output")


def _canon(**chars) -> Canon:
    return Canon(characters={n: c for n, c in chars.items()})


def _draft(prose="본문입니다.", n=3) -> Draft:
    return Draft(episode_number=n, prose=prose)


KEITA = CharacterCard(name="케이타", is_main_cast=True, status="active",
                      current_location="수리소")


# ── extraction ───────────────────────────────────────────────────────────────
def test_a_status_change_becomes_a_character_update():
    llm = StubExtractor(CanonDeltaDraft(character_updates=[
        CharacterUpdateDraft(name="케이타", current_location="관아", condition="부상")]))
    d = extract_canon_delta(llm, _draft(), _canon(케이타=KEITA))
    upd = d.character_updates["케이타"]
    assert (upd.current_location, upd.condition) == ("관아", "부상")
    assert upd.status is None and upd.power_level is None      # unchanged stays unset


def test_an_update_naming_an_unknown_character_is_dropped():
    """A hallucinated name must never conjure a canon card."""
    llm = StubExtractor(CanonDeltaDraft(character_updates=[
        CharacterUpdateDraft(name="존재하지않는인물", condition="부상")]))
    d = extract_canon_delta(llm, _draft(), _canon(케이타=KEITA))
    assert d.character_updates == {}


def test_an_update_with_nothing_changed_is_not_recorded():
    llm = StubExtractor(CanonDeltaDraft(character_updates=[
        CharacterUpdateDraft(name="케이타")]))
    assert extract_canon_delta(llm, _draft(), _canon(케이타=KEITA)).character_updates == {}


def test_a_new_character_is_added_and_stamped_with_its_descriptors():
    llm = StubExtractor(CanonDeltaDraft(new_characters=[
        NewCharacterDraft(name="차수련", descriptors=["관복형 제복"], is_main_cast=False)]))
    d = extract_canon_delta(llm, _draft(), _canon(케이타=KEITA))
    assert d.new_characters["차수련"].immutable_descriptors == ["관복형 제복"]


def test_a_new_character_that_already_exists_is_not_duplicated():
    llm = StubExtractor(CanonDeltaDraft(new_characters=[NewCharacterDraft(name="케이타")]))
    assert extract_canon_delta(llm, _draft(), _canon(케이타=KEITA)).new_characters == {}


def test_a_known_fact_is_stamped_with_the_episode_that_established_it():
    llm = StubExtractor(CanonDeltaDraft(new_known_facts=[
        KnownFactDraft(character="케이타", fact="감사관의 정체를 알게 되었다")]))
    d = extract_canon_delta(llm, _draft(n=7), _canon(케이타=KEITA))
    fact = d.new_known_facts["케이타"][0]
    assert fact.learned_episode == 7 and "감사관" in fact.fact


def test_a_fact_about_an_unknown_character_is_dropped():
    llm = StubExtractor(CanonDeltaDraft(new_known_facts=[
        KnownFactDraft(character="유령", fact="무언가")]))
    assert extract_canon_delta(llm, _draft(), _canon(케이타=KEITA)).new_known_facts == {}


def test_rules_and_terms_already_in_canon_are_not_re_added():
    """Re-appending the same rule every episode would bloat the cached prefix."""
    canon = _canon(케이타=KEITA)
    canon.world_rules.append(WorldRule(text="원격 보정은 불가능하다"))
    canon.glossary.append(GlossaryEntry(term="호패", canonical_form="호패"))
    llm = StubExtractor(CanonDeltaDraft(
        new_world_rules=["원격 보정은 불가능하다", "등급은 혈통으로 정해진다"],
        new_glossary_terms=["호패", "정산상단"]))
    d = extract_canon_delta(llm, _draft(), canon)
    assert [r.text for r in d.new_world_rules] == ["등급은 혈통으로 정해진다"]
    assert [g.canonical_form for g in d.new_glossary] == ["정산상단"]


def test_an_episode_that_changed_nothing_yields_an_empty_delta():
    d = extract_canon_delta(StubExtractor(), _draft(), _canon(케이타=KEITA))
    assert d.new_characters == {} and d.character_updates == {}
    assert d.new_known_facts == {} and not d.new_world_rules and not d.new_glossary


def test_the_extractor_is_shown_current_canon_so_it_reports_only_changes():
    canon = _canon(케이타=KEITA)
    canon.world_rules.append(WorldRule(text="원격 보정은 불가능하다"))
    llm = StubExtractor()
    extract_canon_delta(llm, _draft("본문 내용입니다."), canon)
    sent = llm.prompts[-1]
    assert "케이타" in sent and "원격 보정은 불가능하다" in sent
    assert "본문 내용입니다." in sent


# ── commit ───────────────────────────────────────────────────────────────────
def _store(tmp_path, canon: Canon) -> CanonStore:
    s = CanonStore(tmp_path / "novel")
    s.initialize(genre_profile=genre_profile(), north_star=north_star(),
                 canon=canon, voice_bible=voice_bible())
    return s


def test_canon_accumulates_across_episodes(tmp_path):
    """The whole point: episode N+1's ContextPack must see episode N's facts."""
    s = _store(tmp_path, _canon(케이타=KEITA))
    llm = StubExtractor(CanonDeltaDraft(
        character_updates=[CharacterUpdateDraft(name="케이타", current_location="관아")],
        new_known_facts=[KnownFactDraft(character="케이타", fact="등급증을 잃었다")]))
    canonicalize_episode(llm, s, _draft(n=4))
    card = s.load_canon().characters["케이타"]
    assert card.current_location == "관아"
    assert [f.fact for f in card.known_facts] == ["등급증을 잃었다"]


def test_a_failed_extraction_does_not_lose_the_episode(tmp_path):
    """The ledgers are already committed; a flaky judge costs one episode's
    canon accumulation, not the run."""
    s = _store(tmp_path, _canon(케이타=KEITA))
    before = s.load_canon().model_dump()
    delta = canonicalize_episode(StubExtractor(boom=True), s, _draft())
    assert delta.new_characters == {} and delta.character_updates == {}
    assert s.load_canon().model_dump() == before        # canon untouched


def test_an_author_edit_is_not_silently_reverted(tmp_path):
    """테스트 피드백 2-③: provenance exists so the extractor cannot overwrite a
    correction the author just made by hand. Knowledge still appends — that is
    additive — but the mutable status fields are left alone."""
    canon = _canon(케이타=KEITA)
    canon.last_modified_by = "author"
    canon.characters["케이타"].current_location = "작가가 고친 위치"
    s = _store(tmp_path, canon)

    apply_canon_delta(s, CanonDelta(
        source_episode=4,
        character_updates={"케이타": CharacterUpdate(current_location="모델이 쓴 위치")},
        new_known_facts={"케이타": [KnownFact(fact="새 사실", learned_episode=4)]}))

    card = s.load_canon().characters["케이타"]
    assert card.current_location == "작가가 고친 위치"      # preserved
    assert [f.fact for f in card.known_facts] == ["새 사실"]  # knowledge still appended


def test_a_delta_naming_an_unknown_character_does_not_crash_the_commit(tmp_path):
    """Regression: apply_delta indexed canon.characters[name] directly, so one
    hallucinated name would raise KeyError and take the episode down."""
    s = _store(tmp_path, _canon(케이타=KEITA))
    apply_canon_delta(s, CanonDelta(
        source_episode=4,
        character_updates={"유령": CharacterUpdate(condition="부상")},
        new_known_facts={"유령": [KnownFact(fact="무언가", learned_episode=4)]}))
    assert set(s.load_canon().characters) == {"케이타"}


def test_the_same_fact_is_not_appended_twice(tmp_path):
    s = _store(tmp_path, _canon(케이타=KEITA))
    d = CanonDelta(source_episode=4,
                   new_known_facts={"케이타": [KnownFact(fact="같은 사실", learned_episode=4)]})
    apply_canon_delta(s, d)
    apply_canon_delta(s, d)
    assert len(s.load_canon().characters["케이타"].known_facts) == 1


def test_committing_bumps_the_canon_version(tmp_path):
    s = _store(tmp_path, _canon(케이타=KEITA))
    before = s.load_canon().version
    apply_canon_delta(s, CanonDelta(source_episode=4, new_world_rules=[WorldRule(text="새 규칙")]))
    assert s.load_canon().version == before + 1
