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
    PlannedThread,
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
    ArcMapDraft,
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


def arc_for_episode(arc_map: ArcMap, episode: int) -> Arc | None:
    """The arc an episode belongs to, derived from the spans.

    Never stored as mutable status: the driver retries the SAME episode after a
    failure, so a persisted "active" flag becomes a second source of truth that
    goes stale on every backwards move of the cursor. An episode past the plan
    falls to the last arc rather than losing its picture.
    """
    if not arc_map.arcs:
        return None
    for arc in arc_map.arcs:
        if arc.start_ep <= episode <= arc.end_ep:
            return arc
    return arc_map.arcs[-1] if episode > arc_map.arcs[-1].end_ep else arc_map.arcs[0]


_DETAILED_ARCS = 2          # current + next, per DESIGN §L2


def arc_span_problems(arc_map: ArcMap) -> list[str]:
    """Readable Korean reasons an arc plan does not cover its serial, or [].

    A hole means arc_for_episode falls through mid-serial and the planner
    silently loses its picture, so a hand edit is checked at the moment it is
    made rather than discovered 12 episodes later.
    """
    if not arc_map.arcs:
        return ["부(arc)가 하나도 없습니다"]
    problems = []
    cursor = 1
    for i, arc in enumerate(arc_map.arcs, 1):
        if arc.start_ep != cursor:
            problems.append(
                f"{i}부가 {arc.start_ep}화에서 시작합니다 — {cursor}화여야 이어집니다")
        if arc.end_ep < arc.start_ep:
            problems.append(f"{i}부의 끝({arc.end_ep})이 시작({arc.start_ep})보다 앞섭니다")
        cursor = max(cursor, arc.end_ep + 1)
    total = arc_map.total_episodes
    if total and arc_map.arcs[-1].end_ep != total:
        problems.append(
            f"마지막 부가 {arc_map.arcs[-1].end_ep}화에서 끝납니다 — 총 {total}화입니다")
    return problems


def effective_arc_map(stored: ArcMap | None, llm: LLM,
                      north_star: NorthStar) -> ArcMap:
    """The serial's plan, or the pre-L2 stub for a store that has none.

    One place for the policy: every planning entry point routes through here,
    so the driver, the console, run_setup and revise_episode cannot drift into
    disagreeing about what the story's shape is.
    """
    return stored if stored is not None else seed_arc_map(llm, north_star)


def unplanted_threads(arc_map: ArcMap) -> list[PlannedThread]:
    """Threads the plan calls for that the serial has not thrown yet.

    All of them, not just the current arc's: a spine thread has to be in the
    water long before the arc that closes it, which is the whole reason the
    plan exists.
    """
    return sorted((t for t in arc_map.threads if not t.planted_as),
                  key=lambda t: t.pays_off_in_arc)


def _thread_line(arc_map: ArcMap, thread: PlannedThread) -> str:
    idx = min(max(thread.pays_off_in_arc, 1), len(arc_map.arcs)) - 1
    arc = arc_map.arcs[idx] if arc_map.arcs else None
    where = f"{idx + 1}부({arc.start_ep}-{arc.end_ep}화)" if arc else "미정"
    return (f"- [{thread.thread_id}] ({thread.magnitude.value}) "
            f"{thread.description} → {where}에서 회수")


def _thread_arc_end(arc_map: ArcMap, thread_id: str) -> int | None:
    """The last episode of the arc that pays a planned thread off."""
    thread = next((t for t in arc_map.threads if t.thread_id == thread_id), None)
    if thread is None or not arc_map.arcs:
        return None
    idx = min(max(thread.pays_off_in_arc, 1), len(arc_map.arcs)) - 1
    return arc_map.arcs[idx].end_ep


def _arc_line(arc_map: ArcMap, arc: Arc | None, episode: int) -> str:
    """The current arc as ONE pre-formatted line.

    Four separate slots would be four more things competing for the planner's
    attention, and this repo has measured prompt additions costing quality.
    """
    if arc is None:
        return "미정"
    idx = arc_map.arcs.index(arc) + 1
    parts = [f"{idx}부 · {arc.start_ep}-{arc.end_ep}화 중 {episode}화",
             f"목표: {arc.goal}"]
    for label, value in (("클라이맥스", arc.climax), ("보상", arc.payoff),
                         ("다음 부로", arc.ending_hook)):
        if value:
            parts.append(f"{label}: {value}")
    return " · ".join(parts)


def _cast_roles(canon: Canon) -> str:
    """The cast with what each of them can actually do.

    Names alone were enough for the arc planner to hand a 30-episode structural
    role to the wrong character — canon had one holding the opposing company's
    legal mandate and another holding the stamp, and the plan swapped them. The
    drafter follows the plan, and Track A then blocks every episode.
    """
    lines = []
    for name, card in canon.characters.items():
        bits = [b for b in (", ".join(card.immutable_descriptors),
                            card.power_level) if b]
        lines.append(f"- {name}: " + " · ".join(bits) if bits else f"- {name}")
    return "\n".join(lines) or "미정"


def plan_arcs(llm: LLM, *, north_star: NorthStar, profile: GenreProfile,
              canon: Canon, total_episodes: int) -> ArcMap:
    """L2 ArcPlanner — the overall picture, built once before episode 1.

    Without it the EpisodePlanner invents 떡밥 knowing the premise and the story
    so far but not where any of it is going, so a thread's deadline can only be
    a guess. Spans are repaired rather than trusted: a gap means some episode
    has no arc and the planner would silently lose its picture mid-serial.
    """
    draft = llm.structured(
        [
            {"role": "system", "content": analyst_system()},
            {"role": "user", "content": render(
                "arc_plan", premise=north_star.premise,
                core_conflict=north_star.core_conflict,
                episode_engine=north_star.episode_engine,
                central_twist=north_star.central_twist or "미정",
                hard_rules="; ".join(north_star.hard_rules) or "없음",
                sub_genre=profile.sub_genre,
                catharsis_cadence=profile.target_catharsis_cadence,
                cast=_cast_roles(canon),
                total_episodes=total_episodes)},
        ],
        ArcMapDraft,
    )

    arcs: list[Arc] = []
    cursor = 1
    for i, a in enumerate(draft.arcs):
        if cursor > total_episodes:
            break
        end = min(max(a.end_ep, cursor), total_episodes)
        arcs.append(Arc(
            goal=a.goal, antagonist=a.antagonist, climax=a.climax,
            payoff=a.payoff, ending_hook=a.ending_hook,
            start_ep=cursor, end_ep=end, detailed=i < _DETAILED_ARCS,
        ))
        cursor = end + 1
    if not arcs:
        arcs = [Arc(goal=north_star.core_conflict,
                    climax=north_star.central_twist or "1부 전환점",
                    start_ep=1, end_ep=total_episodes, detailed=True)]
    arcs[-1].end_ep = total_episodes

    threads = [
        PlannedThread(
            thread_id=f"thread-{i:02d}",
            description=t.description,
            magnitude=_magnitude(t.magnitude),
            # A thread pointing past the last arc could never be drawn down.
            pays_off_in_arc=min(max(t.pays_off_in_arc or 1, 1), len(arcs)),
        )
        for i, t in enumerate(draft.threads, 1)
    ]
    return ArcMap(arcs=arcs, threads=threads, total_episodes=total_episodes)


def seed_arc_map(llm: LLM, north_star: NorthStar) -> ArcMap:
    """Minimal first ArcMap so EpisodePlanner is unblocked (invariant #11).
    Phase 1a keeps this thin — one active arc; full ArcPlanner comes later."""
    return ArcMap(
        arcs=[
            Arc(
                goal=north_star.core_conflict,
                climax=north_star.central_twist or "1부 전환점",
                payoff="주인공이 처음으로 판을 뒤집는다",
                start_ep=1,
                end_ep=15,
                detailed=True,
            )
        ]
    )


_BEAT_TYPES = {b.value: b for b in BeatType}


def _beat_type(raw: str) -> BeatType:
    return _BEAT_TYPES.get(raw.strip().lower(), BeatType.SETUP)


def _magnitude(raw: str) -> SeedMagnitude:
    return SeedMagnitude.MAJOR if "major" in raw.lower() else SeedMagnitude.MINOR


def _seed_deadline(raw: int | None, magnitude: SeedMagnitude, episode: int,
                   total: int | None, cadence: int,
                   arc_end: int | None = None) -> int | None:
    """The episode a planted seed must be paid by, or None for an open minor.

    A deadline at or before the episode that plants the seed makes it overdue
    the moment it appears, so it is pushed out rather than honoured.
    """
    if arc_end:
        # Drawn from the plan: the arc that closes the thread IS the horizon,
        # so the number stops being a guess. Used as a clamp and not a value —
        # stamping every thread of an arc with that arc's end would have them
        # all come due, and all ripen, in the same episode.
        return raw if raw and episode < raw <= arc_end else max(arc_end, episode + 1)
    if raw and raw > episode:
        return raw
    if magnitude is not SeedMagnitude.MAJOR:
        return None
    return max(total or (episode + max(1, cadence) * 3), episode + 1)


def _known_thread(arc_map: ArcMap, seed) -> str:
    """The plan thread a seed claims, or "" — a hallucinated id binds nothing.

    The seed is still planted: dropping the episode's 떡밥 over a bad id would
    be worse than losing the binding.
    """
    raw = (seed.planned_thread_id or "").strip().strip("[]()<>").strip().lower()
    return next((t.thread_id for t in arc_map.threads if t.thread_id.lower() == raw), "")


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
    # The last episode resolves instead of cutting. Without this the 완결
    # directive ("남은 떡밥을 모두 회수하고 결말을 내세요") and the static cut
    # rule ("마지막 비트는 진행 중인 상태로 끊습니다") landed in one rendered
    # prompt, and only one of them was rewarded by the gate.
    is_final: bool = False,
    # How long the serial is. Without it the model was asked to name a payoff
    # deadline with no denominator anywhere in the prompt, and the MAJOR
    # fallback below derived a 줄기's lifespan from the 사이다 rhythm.
    total_episodes: int | None = None,
) -> BeatSheet:
    """EpisodePlanner — enforces the rhythm controller and due foreshadows."""
    arc = arc_for_episode(arc_map, episode_number)
    # The plan already records the length it was built for. Requiring every
    # call site to repeat it is how run_setup's preview ended up planned
    # against "총 미정화" while the plan next to it said 30.
    total_episodes = total_episodes or arc_map.total_episodes or None
    due = foreshadow.due(episode_number)
    if extra_directive:
        # Converging: the directive calls a MAJOR payoff mandatory, but this
        # block is the planner's only channel for seed IDs and due() filters by
        # deadline — a seed planted late has a due_by_ep past the target, so its
        # id was never shown and the payoff it demanded was undeclarable.
        seen = {x.seed_id for x in due}
        due = due + [x for x in foreshadow.unpaid_major() if x.seed_id not in seen]
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
                arc_line=_arc_line(arc_map, arc, episode_number),
                central_twist=north_star.central_twist or "미정",
                cast=cast,
                story_so_far=summary.story_so_far or "아직 1화 이전입니다.",
                pacing_directive="\n".join(
                    x for x in (opening_directive(episode_number),
                                rhythm.pacing_directive(), extra_directive) if x),
                # the seed_id must be visible, or the planner has no handle to
                # declare a payoff with — seeds_to_pay would always come back empty
                due_seeds=(chr(10).join(f"- [{x.seed_id}] {x.description}"
                                        for x in due) or "없음"),
                closing_rule=render(
                    "closing_rule_final" if is_final else "closing_rule_serial"),
                planned_threads=(chr(10).join(
                    _thread_line(arc_map, t) for t in unplanted_threads(arc_map))
                    or "없음"),
                ripening_seeds=(chr(10).join(f"- [{x.seed_id}] {x.description}"
                                             for x in foreshadow.ripening(episode_number))
                                or "없음"),
                total_episodes=(total_episodes or "미정"),
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
                # life of the run. It needs a deadline, and the serial's end is
                # the honest one: a MAJOR is by definition a thread the story
                # cannot finish without. The old fallback used the 사이다
                # cadence, which gave a 30화 serial's premise-level thread a
                # 7화 deadline (1 + max(1,2)*3) — a number about payoff rhythm
                # deciding how long the spine may stay open.
                due_by_ep=_seed_deadline(
                    s.due_by_ep, _magnitude(s.magnitude), episode_number,
                    total_episodes, profile.target_catharsis_cadence,
                    arc_end=_thread_arc_end(arc_map, _known_thread(arc_map, s))),
                planned_thread_id=_known_thread(arc_map, s),
            )
            for i, s in enumerate(draft.seeds_to_plant, 1)
        ],
        # only IDs that really exist may be marked paid — a hallucinated id would
        # otherwise silently no-op in the canonicalizer, leaving the 떡밥 open.
        # Normalized first: the prompt renders "[seed-0001]", so a model copying
        # what it sees returns the bracketed form, which bare membership dropped.
        seeds_to_pay=_resolve_seed_ids(draft.seeds_to_pay, foreshadow),
        seeds_to_reinforce=_resolve_seed_ids(draft.seeds_to_reinforce, foreshadow),
        closing_cliffhanger=draft.closing_cliffhanger,
        length_target=profile.episode_length_target,
        pov=profile.pov,
        entities_present=draft.entities_present,
    )
