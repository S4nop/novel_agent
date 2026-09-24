"""LLM-facing DTOs — deliberately SHALLOW (DESIGN §5).

Structured-output schemas accept only a JSON-Schema subset — no `dict` with
arbitrary keys, no self-reference, shallow nesting (true of both Anthropic's
`output_config.format` and Gemini's `responseSchema`). Our domain artifacts
(artifacts.py) use dicts and nested models because that's right for the domain —
so the LLM speaks these flat DTOs and `nodes.py` maps them onto the artifacts.
Rich validation happens in Pydantic AFTER parsing, never in the LLM schema.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class GenreProfileDraft(BaseModel):
    """L0 inferred from the user's freeform idea. Open vocabulary — no enum menu."""
    audience: str
    content_rating: str = Field(description="전연령 | 15+ (노골적 성적 묘사는 범위 밖)")
    sub_genre: str
    trope_checklist: list[str]
    pov: str
    tense: str
    register_baseline: str
    target_catharsis_cadence: int = Field(description="사이다가 최소 몇 화마다 터져야 하는가")
    max_consecutive_frustration_beats: int
    forbidden_anti_patterns: list[str]
    inference_notes: str = Field(description="이 아이디어에서 장르를 이렇게 판단한 근거")


class NorthStarDraft(BaseModel):
    """L1 — the thin, stable spine."""
    title: str = Field(description="키워드가 살아있는 한국 웹소설식 제목")
    premise: str
    core_conflict: str
    protagonist_edge: str
    central_twist: str
    intended_ending: str
    episode_engine: str = Field(description="100화 이상을 지탱할 반복 가능한 갈등 생성기")
    power_system: str = Field(description="명확한 비용과 한계를 가진 능력/규칙 체계")
    hard_rules: list[str]


class CharacterDraft(BaseModel):
    """Flattened CharacterCard + VoiceCard (dicts and deep nesting avoided)."""
    name: str
    is_main_cast: bool
    immutable_descriptors: list[str] = Field(description="변하지 않는 외형/특징")
    speech_register: str
    honorific_pattern: str = Field(description="반말/존댓말 사용 양상")
    speech_tics: list[str]
    exemplar_lines: list[str] = Field(description="이 인물다운 대사 2~3개")
    personality: str
    goals: list[str]
    secrets: list[str]
    current_location: str
    condition: str
    power_level: str


class GlossaryDraft(BaseModel):
    term: str
    canonical_form: str = Field(description="작품에서 고정할 한국어 표기")
    notes: str


class CanonInitDraft(BaseModel):
    """Initial Canon + VoiceBible."""
    characters: list[CharacterDraft]
    hard_world_rules: list[str]
    soft_world_rules: list[str]
    glossary: list[GlossaryDraft]
    voice_spec: str = Field(description="작품 전체의 문체 규격")
    voice_exemplars: list[str] = Field(description="그 문체를 보여주는 예문 2~3개")


class ArcDraft(BaseModel):
    goal: str = Field(description="이 부(arc)에서 주인공이 쫓는 목표")
    antagonist: str = Field(default="", description="이 부의 대립 축")
    climax: str = Field(description="이 부가 터지는 지점")
    payoff: str = Field(description="독자가 이 부에서 받는 보상")
    ending_hook: str = Field(default="", description="다음 부로 넘기는 갈고리")
    start_ep: int = Field(description="이 부가 시작하는 화")
    end_ep: int = Field(description="이 부가 끝나는 화")


class PlannedThreadDraft(BaseModel):
    description: str = Field(description="이 이야기에 반드시 필요한 떡밥 하나")
    magnitude: str = Field(description="major | minor")
    pays_off_in_arc: int = Field(description="몇 번째 부에서 회수되는가 (1부터)")


class ArcMapDraft(BaseModel):
    """L2 — the whole serial broken into arcs, with the threads it needs."""
    arcs: list[ArcDraft]
    threads: list[PlannedThreadDraft]


class BeatDraft(BaseModel):
    text: str
    beat_type: str = Field(description="setup | escalation | payoff | frustration | reveal | cliffhanger")


class SeedDraft(BaseModel):
    description: str
    magnitude: str = Field(description="major | minor")
    due_by_ep: int = Field(description="몇 화까지 회수할 것인가 (0이면 미정)")
    planned_thread_id: str = Field(
        default="",
        description="[계획된 떡밥]에서 가져온 것이면 그 ID(thread-01 형태). "
                    "이 화에서 새로 지어낸 떡밥이면 빈 문자열.")


class BeatSheetDraft(BaseModel):
    """L3 — one episode's plan."""
    opening_hook: str
    beats: list[BeatDraft]
    the_one_progression: str = Field(description="이 화에서 반드시 일어나는 단 하나의 진전")
    closing_cliffhanger: str
    entities_present: list[str]
    seeds_to_plant: list[SeedDraft] = Field(
        default_factory=list,
        description="이 화에서 새로 던지는 떡밥. major = 회수 전에는 완결할 수 없는 "
                    "줄기, minor = 나중에 한 번 되짚으면 끝나는 디테일. 없으면 빈 목록.")
    seeds_to_pay: list[str] = Field(
        default_factory=list,
        description="이 화에서 회수하는 떡밥의 ID 목록. [회수 기한이 된 떡밥]에 대괄호로 "
                    "표시된 ID를 그대로 쓰세요. 회수하지 않으면 빈 목록.")
    seeds_to_reinforce: list[str] = Field(
        default_factory=list,
        description="이 화에서 회수하지 않고 한 번 더 건드리는 떡밥의 ID 목록. "
                    "[흔들 때가 된 떡밥]에 표시된 ID를 그대로 쓰세요.")


# ── Track A continuity (DESIGN §3) ───────────────────────────────────────────
class ContinuityFindingDraft(BaseModel):
    """One contradiction between the prose and canon. Shallow by design."""
    canon_fact: str = Field(description="캐논에 적힌 사실 (그대로 인용)")
    prose_claim: str = Field(description="본문이 주장하는 내용 (그대로 인용)")
    severity: str = Field(description="blocker | major | minor 중 하나")
    where: str = Field(default="", description="본문에서 문제가 된 짧은 구절")


class ContinuityReportDraft(BaseModel):
    findings: list[ContinuityFindingDraft] = Field(default_factory=list)


# ── Canonicalizer, LLM half (DESIGN §3, 클로드 제안 1) ────────────────────────
class CharacterUpdateDraft(BaseModel):
    """Mutable current-status only. Empty string = unchanged."""
    name: str = Field(description="캐논에 이미 있는 인물 이름")
    status: str = Field(default="", description="변했을 때만: active/부상/투옥/사망 등")
    current_location: str = Field(default="", description="변했을 때만")
    condition: str = Field(default="", description="변했을 때만")
    power_level: str = Field(default="", description="변했을 때만")
    new_aliases: list[str] = Field(default_factory=list, description="이 화에서 새로 불린 호칭")


class KnownFactDraft(BaseModel):
    character: str = Field(description="이 사실을 알게 된 인물")
    fact: str = Field(description="이 화에서 확정된 사실 한 줄")


class NewCharacterDraft(BaseModel):
    name: str
    descriptors: list[str] = Field(default_factory=list, description="변하지 않는 외형·특징")
    is_main_cast: bool = False


class CanonDeltaDraft(BaseModel):
    """What this episode CHANGED. Empty lists when nothing changed."""
    character_updates: list[CharacterUpdateDraft] = Field(default_factory=list)
    new_known_facts: list[KnownFactDraft] = Field(default_factory=list)
    new_characters: list[NewCharacterDraft] = Field(default_factory=list)
    new_world_rules: list[str] = Field(default_factory=list)
    new_glossary_terms: list[str] = Field(default_factory=list)


# ── Track B craft judging (DESIGN §3, 클로드 제안 5) ──────────────────────────
class CraftFindingDraft(BaseModel):
    """One craft problem, with the evidence that justifies it."""
    dimension: str = Field(description="plot | character | genre 중 하나")
    problem: str = Field(description="무엇이 문제인지 한 문장")
    evidence: str = Field(description="본문에서 근거가 되는 짧은 인용")
    severity: str = Field(description="major | minor 중 하나")


class CraftReportDraft(BaseModel):
    findings: list[CraftFindingDraft] = Field(default_factory=list)
    payoff_landed: bool = Field(
        default=True,
        description="이 화에 독자가 보상(사이다)으로 느낄 장면이 실제로 있었는가. "
                    "비트시트가 payoff/reveal로 계획했더라도 본문에서 그 해소가 "
                    "일어나지 않았으면 false.")


# ── hook / cliffhanger (연독률의 두 지점) ─────────────────────────────────────
class OpeningEndingFindingDraft(BaseModel):
    part: str = Field(description="hook | cliffhanger 중 하나")
    problem: str = Field(description="왜 실패인지 한 문장")
    evidence: str = Field(description="본문에서 근거가 되는 짧은 인용")


class OpeningEndingReportDraft(BaseModel):
    findings: list[OpeningEndingFindingDraft] = Field(default_factory=list)
