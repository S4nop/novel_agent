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
from .prompts import analyst_system, restraint, voice_spec_guidance
from .prompt_store import render
from .schemas import (
    BeatSheetDraft,
    CanonInitDraft,
    GenreProfileDraft,
    NorthStarDraft,
)

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
            {"role": "system", "content": analyst_system()},
            {"role": "user", "content": render("genre_inference", idea=idea)},
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
                    {"role": "system", "content": analyst_system()},
                    {"role": "user", "content": render(
                        "northstar", idea=idea, audience=profile.audience,
                        sub_genre=profile.sub_genre,
                        tropes=", ".join(profile.trope_checklist),
                        angle=angle,
                        prior=(("[중복 금지]" + chr(10) + prior) if prior else ""),
                        restraint=restraint())},
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
            {"role": "system", "content": analyst_system()},
            {"role": "user", "content": render(
                "canon_init", idea=idea, premise=north_star.premise,
                core_conflict=north_star.core_conflict,
                protagonist_edge=north_star.protagonist_edge,
                episode_engine=north_star.episode_engine,
                hard_rules="; ".join(north_star.hard_rules),
                audience=profile.audience, sub_genre=profile.sub_genre,
                pov=profile.pov, restraint=restraint(),
                voice_spec_guidance=voice_spec_guidance())},
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


def _resolve_seed_ids(raw: list[str], foreshadow: ForeshadowLedger) -> list[str]:
    """Map whatever the planner wrote back onto real seed ids.

    Tolerates the bracketed form the prompt itself displays, stray whitespace,
    case, and a trailing description. Anything still unmatched is dropped — but
    silently dropping a VALID id was the bug, not the guard.
    """
    by_lower = {k.lower(): k for k in foreshadow.seeds}
    out: list[str] = []
    for item in raw or []:
        token = str(item).strip().strip("[]()<>").strip()
        key = by_lower.get(token.lower())
        if key is None:                      # "seed-0001 (사라진 호패)" → first word
            key = by_lower.get(token.split()[0].lower()) if token.split() else None
        if key and key not in out:
            out.append(key)
    return out


# Episode 1 has a different job from episode 40, and nothing in the pipeline
# knew that: the planner prompt, the restraint rules and the drafter rules are
# all tuned for mid-serial, where the reader already has the world. Applied to
# 1화 they produce competent prose that starts in the middle of a story the
# reader has not been told — no ground, no direction. 연독률 depends on 1화 more
# than on any other episode, so this is the expensive place to get it wrong.
def opening_directive(episode_number: int) -> str:
    """Orientation duty for the first episodes. Empty from episode 3 on."""
    if episode_number == 1:
        return (
            "[1화 — 독자는 이 세계를 처음 봅니다]\n"
            "- 이 세계를 지배하는 규칙 하나를 '장면으로' 보여주세요. 설명하지 말고, "
            "인물이 그 규칙 때문에 손해를 보거나 굽히는 장면으로 드러내세요.\n"
            "- 주인공이 무엇을 원하고 무엇이 그것을 막는지 이 화 안에서 분명히 하세요. "
            "독자가 '이 사람이 앞으로 무엇과 싸우겠구나'를 알 수 있어야 합니다.\n"
            "- 이야기가 어디로 갈지에 대한 약속을 하나 남기세요. 사건의 크기든 인물의 "
            "목표든, 다음 화를 볼 이유는 절단신공만으로 만들지 마세요.\n"
            "- 이미 진행 중인 이야기의 중간처럼 시작하지 마세요. 훅은 강하되, 독자가 "
            "발 디딜 곳은 주어야 합니다."
        )
    if episode_number == 2:
        return ("[2화] 1화에서 세운 세계 규칙과 주인공의 목표를 한 번 더 구체적으로 "
                "확인시키세요. 아직 독자는 이 세계에 익숙하지 않습니다.")
    return ""


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
    # Convergence pressure from the driver (완결 approach). Merged into the
    # pacing directive rather than added as a new prompt slot, so an edited
    # episode_plan.md cannot accidentally drop it.
    extra_directive: str = "",
) -> BeatSheet:
    """EpisodePlanner — enforces the rhythm controller and due foreshadows."""
    arc = next((a for a in arc_map.arcs if a.status == "active"), None)
    due = foreshadow.due(episode_number)
    cast = ", ".join(canon.characters)

    draft = llm.structured(
        [
            {"role": "system", "content": analyst_system()},
            {"role": "user", "content": render(
                "episode_plan", premise=north_star.premise,
                episode_engine=north_star.episode_engine,
                hard_rules="; ".join(north_star.hard_rules),
                sub_genre=profile.sub_genre,
                catharsis_cadence=profile.target_catharsis_cadence,
                max_frustration=profile.max_consecutive_frustration_beats,
                forbidden=", ".join(profile.forbidden_anti_patterns) or "없음",
                arc_goal=(arc.goal if arc else "미정"),
                arc_payoff=(arc.payoff if arc else ""),
                cast=cast,
                story_so_far=summary.story_so_far or "아직 1화 이전입니다.",
                pacing_directive="\n".join(
                    x for x in (opening_directive(episode_number),
                                rhythm.pacing_directive(), extra_directive) if x),
                # the seed_id must be visible, or the planner has no handle to
                # declare a payoff with — seeds_to_pay would always come back empty
                due_seeds=(chr(10).join(f"- [{x.seed_id}] {x.description}"
                                        for x in due) or "없음"),
                episode_number=episode_number)},
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
                # A MAJOR seed with no deadline is unpayable: due() skips it, so
                # its id is never shown to the planner, while unpaid_major()
                # counts it forever — completion_ready() would stay False for the
                # life of the run. Give it the genre's catharsis window instead.
                due_by_ep=(s.due_by_ep or None) or (
                    episode_number + max(1, profile.target_catharsis_cadence) * 3
                    if _magnitude(s.magnitude) is SeedMagnitude.MAJOR else None),
            )
            for i, s in enumerate(draft.seeds_to_plant, 1)
        ],
        # only IDs that really exist may be marked paid — a hallucinated id would
        # otherwise silently no-op in the canonicalizer, leaving the 떡밥 open.
        # Normalized first: the prompt renders "[seed-0001]", so a model copying
        # what it sees returns the bracketed form, which bare membership dropped.
        seeds_to_pay=_resolve_seed_ids(draft.seeds_to_pay, foreshadow),
        closing_cliffhanger=draft.closing_cliffhanger,
        length_target=profile.episode_length_target,
        pov=profile.pov,
        entities_present=draft.entities_present,
    )
