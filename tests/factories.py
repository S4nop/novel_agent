"""Minimal valid artifact builders for tests (real objects, no mocks)."""
from novel_agent.artifacts import (
    Beat,
    BeatSheet,
    BeatType,
    Canon,
    CharacterCard,
    GenreProfile,
    GlossaryEntry,
    NorthStar,
    Summary,
    VoiceBible,
    VoiceCard,
)


def genre_profile(**kw) -> GenreProfile:
    base = dict(audience="남성향", sub_genre="헌터/회귀", trope_checklist=["회귀", "각성"])
    return GenreProfile(**{**base, **kw})


def north_star(**kw) -> NorthStar:
    base = dict(
        premise="멸망을 본 최약체 헌터가 회귀한다",
        core_conflict="미래 지식 대 정해진 파국",
        protagonist_edge="게이트를 포식해 성장하는 고유 능력",
        episode_engine="다음 게이트 공략 → 서열 역전",
        power_system="포식으로 스킬 흡수, 각성 단계 존재",
        hard_rules=["죽은 자는 되살아나지 않는다"],
    )
    return NorthStar(**{**base, **kw})


def hero() -> CharacterCard:
    return CharacterCard(
        name="김현우",
        is_main_cast=True,
        immutable_descriptors=["검은 머리", "왼손 흉터"],
        voice=VoiceCard(
            speech_register="냉정하고 건조", honorific_pattern="반말",
            speech_tics=["…쯧"], exemplar_lines=["이번엔, 다르게 간다."],
        ),
        current_location="서울 1번 게이트",
        power_level="F급",
    )


def canon(**kw) -> Canon:
    base = dict(
        characters={"김현우": hero()},
        glossary=[GlossaryEntry(term="gate", canonical_form="게이트")],
    )
    return Canon(**{**base, **kw})


def voice_bible() -> VoiceBible:
    return VoiceBible(spec="빠른 호흡, 짧은 문단, 사이다 중심", exemplar_passages=["문이 열렸다."])


def beat_sheet(episode_number: int = 1, **kw) -> BeatSheet:
    base = dict(
        episode_number=episode_number,
        opening_hook="눈을 뜨니 회귀 3일 전이었다",
        the_one_progression="첫 게이트의 위치를 선점한다",
        beats=[Beat(text="회귀를 자각", beat_type=BeatType.SETUP),
               Beat(text="첫 사냥 성공", beat_type=BeatType.PAYOFF)],
        closing_cliffhanger="게이트 너머에서 낯익은 목소리",
    )
    return BeatSheet(**{**base, **kw})


def summary(**kw) -> Summary:
    return Summary(**kw)
