"""Setup + planning nodes (DESIGN §3).

The chain that turns a user's freeform Korean idea into everything the Drafter
needs — so no creative content is hand-authored by the developer:

    Idea ──IdeaIntake──▶ GenreProfile (L0)      ▣ human confirm
         ──NorthStarArchitect──▶ NorthStar (L1) ▣ premise lock (best-of-N)
         ──init_canon_and_voice──▶ Canon, VoiceBible ▣ voice lock
         ──plan_episode──▶ BeatSheet (L3)

Each node is a thin stateless function `f(llm, inputs) -> artifact`: the LLM
returns a shallow DTO (schemas.py), the node maps it onto the domain artifact.
All prompts are Korean — the users are Korean (DESIGN §5).
"""
from __future__ import annotations

from .artifacts import (
    Arc,
    ArcMap,
    Beat,
    BeatSheet,
    BeatType,
    Canon,
    CharacterCard,
    ContentRating,
    GenreProfile,
    GlossaryEntry,
    NorthStar,
    PlannedSeed,
    SeedMagnitude,
    Summary,
    VoiceBible,
    VoiceCard,
    WorldRule,
)
from .ledgers import ForeshadowLedger, RhythmState
from .llm import LLM
from .prompts import VOICE_SPEC_GUIDANCE
from .schemas import (
    BeatSheetDraft,
    CanonInitDraft,
    GenreProfileDraft,
    NorthStarDraft,
)

_ANALYST = (
    "당신은 한국 웹소설 시장을 잘 아는 기획자입니다. "
    "출력은 모두 한국어로 작성합니다. "
    "노골적인 성적 묘사가 필요한 기획은 만들지 않습니다(정책상 범위 밖). "
    "어두운 전개·풍자·폭력·복수는 장르에 맞게 활용할 수 있습니다."
)

# The failure this prevents: from "네오 조선의 흑인 홍길동, 코믹" the agent invented
# 생체 데이터 수탈 + 상평통보 코인 + 넙적패드 + 전뇌 기방… and the stacked concepts
# read as childish. Restraint is a hard requirement, not a preference.
RESTRAINT = """[설정 작법 — 반드시 지킬 것]
■ 억지 조어 금지 (가장 중요)
- 조선 어휘와 기술 어휘를 붙여 만든 신조어를 만들지 마세요.
  나쁜 예: '상평통보 코인', '넙적패드', '전뇌 기방', '생체 데이터 수탈'.
  이런 조어는 귀엽게 보이려는 시도로 읽히고, 그것이 유치함의 정체입니다.
- 기술은 **평범한 사이버펑크 어휘 그대로** 씁니다(단말, 의체, 전선, 노점, 총, 칩).
  굳이 조선식으로 번역하거나 합성하지 마세요.
- 조선은 명사가 아니라 **신분·예법·말투·공간**에서 드러냅니다. 양반과 상놈의 위계,
  존대와 하대, 관아와 저잣거리 — 이런 것이 조선을 만듭니다. 물건 이름이 아닙니다.

■ 절제
- 고유명사와 신조어는 최소한으로. 1화에 새로 등장하는 고유명사는 5개 이하.
- 한 문장에 기술 용어는 하나까지. 문장마다 세계관을 상기시키지 마세요.
- 설정은 배경입니다. 매 장면의 주인은 사람과 사건이어야 합니다.
- 작가가 '이 세계에 없어야 한다'고 답한 것은 절대 넣지 마세요.
- 작가가 정한 기술 수준을 그대로 따르세요. 임의로 더 얹거나 덜어내지 마세요."""

# Platform norms are keyed by PLATFORM, never by genre (invariant #1).
PLATFORM_NORMS = {
    "novelpia": {"episode_length_target": 5200, "register_baseline": "문어체 서술 + 짧은 문단"},
}


def _rating(raw: str) -> ContentRating:
    """Map the model's free text onto our enum; explicit-19+ is out of scope."""
    if "전연령" in raw:
        return ContentRating.ALL
    if "15" in raw:
        return ContentRating.T15
    return ContentRating.MATURE


def infer_genre_profile(llm: LLM, idea: str, *, platform: str = "novelpia") -> tuple[GenreProfile, str]:
    """IdeaIntake & GenreInference — the ONE place genre is decided (DESIGN §3).

    Genre is inferred from the idea's own signals in open vocabulary; only
    PLATFORM-keyed defaults are injected by code.
    """
    norms = PLATFORM_NORMS[platform]
    draft = llm.structured(
        [
            {"role": "system", "content": _ANALYST},
            {
                "role": "user",
                "content": (
                    "다음은 작가가 던진 자유 형식의 아이디어입니다. 이 아이디어 자체의 신호만 읽고 "
                    "웹소설 장르 프로파일을 추론하세요. 정해진 장르 목록에서 고르지 말고, "
                    "이 작품에 맞는 표현으로 직접 이름 붙이세요.\n\n"
                    f"[아이디어]\n{idea}\n\n"
                    "사이다 주기와 최대 연속 고구마는 반드시 구체적인 숫자로 정하세요. "
                    "이 작품에서 특히 피해야 할 안티패턴도 함께 적으세요."
                ),
            },
        ],
        GenreProfileDraft,
    )
    profile = GenreProfile(
        audience=draft.audience,
        content_rating=_rating(draft.content_rating),
        sub_genre=draft.sub_genre,
        trope_checklist=draft.trope_checklist,
        episode_length_target=norms["episode_length_target"],
        pov=draft.pov,
        tense=draft.tense,
        register_baseline=draft.register_baseline or norms["register_baseline"],
        target_catharsis_cadence=max(1, draft.target_catharsis_cadence),
        max_consecutive_frustration_beats=max(1, draft.max_consecutive_frustration_beats),
        forbidden_anti_patterns=draft.forbidden_anti_patterns,
    )
    return profile, draft.inference_notes


def generate_northstar_candidates(
    llm: LLM, idea: str, profile: GenreProfile, *, n: int = 3
) -> list[NorthStarDraft]:
    """NorthStarArchitect — best-of-N diverse candidates for the human PREMISE GATE."""
    candidates: list[NorthStarDraft] = []
    angles = [
        "가장 상업적으로 안전한 노선: 장르 관습을 정확히 지키고 사이다를 최대화",
        "가장 신선한 노선: 같은 소재라도 남들이 안 쓴 구조·직업·관계로 비틀기",
        "주제를 정면으로 다루는 노선: 이 소재가 원래 품고 있는 사회적 주제를 오락으로 승화",
    ]
    for i in range(n):
        angle = angles[i % len(angles)]
        prior = "\n".join(f"- 이미 나온 안: {c.title} / {c.premise}" for c in candidates)
        candidates.append(
            llm.structured(
                [
                    {"role": "system", "content": _ANALYST},
                    {
                        "role": "user",
                        "content": (
                            f"[아이디어]\n{idea}\n\n"
                            f"[장르 프로파일]\n{profile.audience} · {profile.sub_genre} · "
                            f"트로프: {', '.join(profile.trope_checklist)}\n\n"
                            f"[이번 안의 방향]\n{angle}\n\n"
                            f"{('[중복 금지]' + chr(10) + prior) if prior else ''}\n\n"
                            "이 작품의 노스스타(L1)를 설계하세요. 특히 '에피소드 엔진'은 "
                            "100화 이상 반복 가능한 갈등 생성기여야 하고, 파워/규칙 체계에는 "
                            "명확한 비용과 한계가 있어야 합니다.\n\n"
                            f"{RESTRAINT}"
                        ),
                    },
                ],
                NorthStarDraft,
            )
        )
    return candidates


def to_north_star(draft: NorthStarDraft, profile: GenreProfile) -> NorthStar:
    return NorthStar(
        genre_profile_version=profile.version,
        premise=draft.premise,
        core_conflict=draft.core_conflict,
        protagonist_edge=draft.protagonist_edge,
        central_twist=draft.central_twist,
        intended_ending=draft.intended_ending,
        episode_engine=draft.episode_engine,
        power_system=draft.power_system,
        hard_rules=draft.hard_rules,
    )


def init_canon_and_voice(
    llm: LLM, idea: str, profile: GenreProfile, north_star: NorthStar
) -> tuple[Canon, VoiceBible]:
    """Canon & VoiceBible Initializer — the setup gate before episode 1."""
    draft = llm.structured(
        [
            {"role": "system", "content": _ANALYST},
            {
                "role": "user",
                "content": (
                    f"[아이디어]\n{idea}\n\n"
                    f"[노스스타]\n전제: {north_star.premise}\n핵심 갈등: {north_star.core_conflict}\n"
                    f"주인공 엣지: {north_star.protagonist_edge}\n엔진: {north_star.episode_engine}\n"
                    f"규칙: {'; '.join(north_star.hard_rules)}\n\n"
                    f"[장르]\n{profile.audience} · {profile.sub_genre} · 시점 {profile.pov}\n\n"
                    "1화를 쓰기 위한 초기 설정집(캐논)과 보이스 바이블을 만드세요.\n"
                    "- 인물은 주인공 1명 + 1화에 실제로 필요한 인물 1~3명만.\n"
                    "- 각 인물의 '변하지 않는 특징'과 말투(반말/존댓말 양상, 말버릇, 대사 예시)를 구체적으로.\n"
                    "- 고유명사 표기를 용어집에 고정하세요(작품 내내 이 표기를 씁니다).\n"
                    "- 보이스 바이블은 이 작품만의 문체 규격 + 그것을 보여주는 예문.\n"
                    "- 말버릇은 인물당 하나만, 남용되지 않을 정도로만 정하세요.\n"
                    "- 용어집은 꼭 필요한 3개 이하로. 있어도 되는 용어는 만들지 마세요.\n\n"
                    f"{RESTRAINT}\n\n{VOICE_SPEC_GUIDANCE}"
                ),
            },
        ],
        CanonInitDraft,
    )

    characters: dict[str, CharacterCard] = {}
    for c in draft.characters:
        characters[c.name] = CharacterCard(
            name=c.name,
            is_main_cast=c.is_main_cast,
            immutable_descriptors=c.immutable_descriptors,
            voice=VoiceCard(
                speech_register=c.speech_register,
                honorific_pattern=c.honorific_pattern,
                speech_tics=c.speech_tics,
                exemplar_lines=c.exemplar_lines,
            ),
            personality=c.personality,
            goals=c.goals,
            secrets=c.secrets,
            current_location=c.current_location,
            condition=c.condition,
            power_level=c.power_level,
        )

    canon = Canon(
        genre_profile_version=profile.version,
        characters=characters,
        world_rules=(
            [WorldRule(text=t, hard=True) for t in draft.hard_world_rules]
            + [WorldRule(text=t, hard=False) for t in draft.soft_world_rules]
        ),
        glossary=[
            GlossaryEntry(term=g.term, canonical_form=g.canonical_form, notes=g.notes)
            for g in draft.glossary
        ],
    )
    voice = VoiceBible(
        spec=draft.voice_spec,
        exemplar_passages=draft.voice_exemplars,
        genre_profile_version=profile.version,
    )
    return canon, voice


def seed_arc_map(llm: LLM, north_star: NorthStar) -> ArcMap:
    """Minimal first ArcMap so EpisodePlanner is unblocked (invariant #11).
    Phase 1a keeps this thin — one active arc; full ArcPlanner comes later."""
    return ArcMap(
        arcs=[
            Arc(
                goal=north_star.core_conflict,
                climax=north_star.central_twist or "1부 전환점",
                payoff="주인공이 처음으로 판을 뒤집는다",
                episode_span="1-15",
                status="active",
                detailed=True,
            )
        ]
    )


_BEAT_TYPES = {b.value: b for b in BeatType}


def _beat_type(raw: str) -> BeatType:
    return _BEAT_TYPES.get(raw.strip().lower(), BeatType.SETUP)


def _magnitude(raw: str) -> SeedMagnitude:
    return SeedMagnitude.MAJOR if "major" in raw.lower() else SeedMagnitude.MINOR


def plan_episode(
    llm: LLM,
    *,
    episode_number: int,
    profile: GenreProfile,
    north_star: NorthStar,
    canon: Canon,
    arc_map: ArcMap,
    rhythm: RhythmState,
    foreshadow: ForeshadowLedger,
    summary: Summary,
) -> BeatSheet:
    """EpisodePlanner — enforces the rhythm controller and due foreshadows."""
    arc = next((a for a in arc_map.arcs if a.status == "active"), None)
    due = foreshadow.due(episode_number)
    cast = ", ".join(canon.characters)

    draft = llm.structured(
        [
            {"role": "system", "content": _ANALYST},
            {
                "role": "user",
                "content": (
                    f"[작품]\n{north_star.premise}\n엔진: {north_star.episode_engine}\n"
                    f"규칙: {'; '.join(north_star.hard_rules)}\n\n"
                    f"[장르]\n{profile.sub_genre} · 사이다 주기 {profile.target_catharsis_cadence}화 이내 · "
                    f"최대 연속 고구마 {profile.max_consecutive_frustration_beats}회\n"
                    f"금지: {', '.join(profile.forbidden_anti_patterns) or '없음'}\n\n"
                    f"[현재 아크]\n{arc.goal if arc else '미정'} → {arc.payoff if arc else ''}\n\n"
                    f"[등장 가능 인물]\n{cast}\n\n"
                    f"[지금까지]\n{summary.story_so_far or '아직 1화 이전입니다.'}\n\n"
                    f"[페이싱 지시]\n{rhythm.pacing_directive()}\n\n"
                    f"[회수 기한이 된 떡밥]\n"
                    + ("\n".join(f"- {s.description}" for s in due) or "없음")
                    + f"\n\n{episode_number}화의 비트시트를 설계하세요. "
                    "도입은 즉시 몰입되는 훅, 마지막은 다음 화를 반드시 보게 만드는 절단으로. "
                    "beat_type은 setup/escalation/payoff/frustration/reveal/cliffhanger 중에서만 쓰세요."
                ),
            },
        ],
        BeatSheetDraft,
    )

    return BeatSheet(
        episode_number=episode_number,
        opening_hook=draft.opening_hook,
        beats=[Beat(text=b.text, beat_type=_beat_type(b.beat_type)) for b in draft.beats],
        the_one_progression=draft.the_one_progression,
        seeds_to_plant=[
            PlannedSeed(
                proposed_seed_id=f"p{episode_number}-{i}",
                description=s.description,
                magnitude=_magnitude(s.magnitude),
                due_by_ep=s.due_by_ep or None,
            )
            for i, s in enumerate(draft.seeds_to_plant, 1)
        ],
        closing_cliffhanger=draft.closing_cliffhanger,
        length_target=profile.episode_length_target,
        pov=profile.pov,
        entities_present=draft.entities_present,
    )
