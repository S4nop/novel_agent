"""Shared data artifacts — the vocabulary every component reads/writes (DESIGN §1).

These Pydantic models are the single source of truth: they back persistence,
the FastAPI schemas (later), and the LLM structured-output schema. Structured
output is a subset of JSON Schema on every provider, so schemas are kept SHALLOW
(no self-referential models; ≤2–3 nesting levels); rich constraints are
enforced in validators after parsing, not in the LLM-facing schema.

All genre-specific behavior lives inside GenreProfile as data — no component
branches on genre.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


# ── enums ────────────────────────────────────────────────────────────────────
class ContentRating(str, Enum):
    # Explicit-19+ is out of scope (provider usage policy — see DESIGN.md §5;
    # Anthropic prohibits sexually explicit content regardless of framing).
    # MATURE = dark/violent/suggestive but non-explicit.
    ALL = "전연령"
    T15 = "15+"
    MATURE = "15+ 마이너/성인정서(비노골)"


class SeedMagnitude(str, Enum):
    MAJOR = "major"
    MINOR = "minor"


class SeedStatus(str, Enum):
    PLANNED = "planned"
    PLANTED = "planted"
    REINFORCED = "reinforced"
    PAID = "paid"
    ABANDONED = "abandoned"


class BeatType(str, Enum):
    SETUP = "setup"
    ESCALATION = "escalation"
    PAYOFF = "payoff"          # 사이다
    FRUSTRATION = "frustration"  # 고구마
    REVEAL = "reveal"
    CLIFFHANGER = "cliffhanger"


# ── L0 / L1 ────────────────────────────────────────────────────────────────
class GenreProfile(BaseModel):
    """L0 — the object that makes every downstream rubric genre-specific.
    content_rating drives rubric/register only (one model serves all ratings)."""
    version: int = 1
    audience: str                                  # e.g. 남성향 / 여성향 / BL
    content_rating: ContentRating = ContentRating.ALL
    sub_genre: str
    trope_checklist: list[str] = Field(default_factory=list)
    episode_length_target: int = 5200              # chars incl. spaces (공백 포함)
    pov: str = "1인칭"
    tense: str = "과거"
    register_baseline: str = ""
    target_catharsis_cadence: int = 3              # a payoff must land every ≤ N eps
    max_consecutive_frustration_beats: int = 2
    forbidden_anti_patterns: list[str] = Field(default_factory=list)


class NorthStar(BaseModel):
    """L1 — thin, stable spine. Its hard_rules feed the cache-stable prefix."""
    genre_profile_version: int = 1
    premise: str
    core_conflict: str
    protagonist_edge: str
    central_twist: str = ""
    intended_ending: str = ""
    episode_engine: str = ""                       # the repeatable conflict generator
    power_system: str = ""
    hard_rules: list[str] = Field(default_factory=list)


# ── canon ────────────────────────────────────────────────────────────────────
class VoiceCard(BaseModel):
    speech_register: str = ""                      # tone/formality of speech
    honorific_pattern: str = ""                    # 반말 / 존댓말 usage
    speech_tics: list[str] = Field(default_factory=list)
    exemplar_lines: list[str] = Field(default_factory=list)


class KnownFact(BaseModel):
    fact: str
    learned_episode: int


class CharacterCard(BaseModel):
    name: str
    aliases: list[str] = Field(default_factory=list)
    is_main_cast: bool = False
    # immutable descriptors + voice go into the cache-stable prefix
    immutable_descriptors: list[str] = Field(default_factory=list)
    voice: VoiceCard = Field(default_factory=VoiceCard)
    personality: str = ""
    goals: list[str] = Field(default_factory=list)
    secrets: list[str] = Field(default_factory=list)
    # current-status fields — mutable, updated per episode (NOT append-only)
    status: str = "active"
    current_location: str = ""
    condition: str = ""
    power_level: str = ""
    # append-only knowledge
    known_facts: list[KnownFact] = Field(default_factory=list)
    relationships: dict[str, str] = Field(default_factory=dict)  # name → typed relation


class WorldRule(BaseModel):
    text: str
    hard: bool = True                              # hard-constraint vs soft


class GlossaryEntry(BaseModel):
    term: str
    canonical_form: str                            # canonical 고유명사 spelling
    notes: str = ""


class Canon(BaseModel):
    version: int = 0
    genre_profile_version: int = 1
    # provenance: an author edit must not be silently overwritten by the
    # Canonicalizer later (reviewer suggestion, added before that node exists)
    last_modified_by: str = "llm"      # "llm" | "author"
    characters: dict[str, CharacterCard] = Field(default_factory=dict)
    world_rules: list[WorldRule] = Field(default_factory=list)
    glossary: list[GlossaryEntry] = Field(default_factory=list)

    def main_cast(self) -> list[CharacterCard]:
        return [c for c in self.characters.values() if c.is_main_cast]


class VoiceBible(BaseModel):
    """Human-locked authorial voice (DESIGN §1)."""
    spec: str = ""
    exemplar_passages: list[str] = Field(default_factory=list)
    genre_profile_version: int = 1


# ── foreshadow seeds ─────────────────────────────────────────────────────────
class ForeshadowSeed(BaseModel):
    seed_id: str
    description: str
    magnitude: SeedMagnitude = SeedMagnitude.MINOR
    planted_ep: int | None = None
    reinforced_in: list[int] = Field(default_factory=list)
    intended_payoff: str = ""
    due_by_ep: int | None = None
    status: SeedStatus = SeedStatus.PLANNED


# ── planning (L2 / L3) ───────────────────────────────────────────────────────
class Beat(BaseModel):
    text: str
    beat_type: BeatType


class PlannedSeed(BaseModel):
    proposed_seed_id: str                          # planner proposes; canonicalizer mints canonical id
    description: str
    magnitude: SeedMagnitude = SeedMagnitude.MINOR
    due_by_ep: int | None = None


class BeatSheet(BaseModel):
    """L3 — one episode's plan."""
    episode_number: int
    opening_hook: str
    beats: list[Beat] = Field(default_factory=list)
    the_one_progression: str = ""
    seeds_to_plant: list[PlannedSeed] = Field(default_factory=list)
    seeds_to_pay: list[str] = Field(default_factory=list)   # canonical seed_ids
    closing_cliffhanger: str = ""
    length_target: int = 5200
    pov: str = "1인칭"
    entities_present: list[str] = Field(default_factory=list)

    def beat_types(self) -> list[BeatType]:
        return [b.beat_type for b in self.beats]


class ArcMap(BaseModel):
    """L2 — current + next arc detailed, rest as loglines (represented as arcs list)."""
    arcs: list["Arc"] = Field(default_factory=list)


class Arc(BaseModel):
    goal: str
    antagonist: str = ""
    climax: str = ""
    payoff: str = ""
    ending_hook: str = ""
    episode_span: str = ""
    status: str = "planned"                        # planned / active / done
    detailed: bool = False                         # False = logline-only


# ── derived / runtime artifacts ──────────────────────────────────────────────
class FactRequest(BaseModel):
    """The Drafter's request for a canon fact it needs but lacks (DESIGN §1).
    A blocking request halts the episode before review (invariant #9)."""
    question: str
    blocking: bool = True


class Draft(BaseModel):
    """One episode's prose as produced by the Drafter (pre-acceptance)."""
    episode_number: int
    prose: str
    fact_requests: list[FactRequest] = Field(default_factory=list)

    @property
    def char_count(self) -> int:
        """Korean web-novel length is measured in 자, 공백 포함."""
        return len(self.prose)

    def has_blocking_fact_request(self) -> bool:
        return any(f.blocking for f in self.fact_requests)


class Summary(BaseModel):
    """Named rolling summaries, multi-resolution (DESIGN §1)."""
    story_so_far: str = ""
    current_arc: str = ""


class EpisodeRecord(BaseModel):
    episode_number: int
    prose: str
    accepted_draft_hash: str
    human_edited: bool = False
    beat_tags: list[BeatType] = Field(default_factory=list)


class CharacterUpdate(BaseModel):
    """Mutable current-status changes only (not append-only knowledge)."""
    status: str | None = None
    current_location: str | None = None
    condition: str | None = None
    power_level: str | None = None
    add_aliases: list[str] = Field(default_factory=list)
    add_relationships: dict[str, str] = Field(default_factory=dict)


class CanonDelta(BaseModel):
    """State changes committed to Canon after acceptance (DESIGN §3, invariant #8).
    Append-only for knowledge/canon history; current-status fields may change."""
    source_episode: int
    new_characters: dict[str, CharacterCard] = Field(default_factory=dict)
    character_updates: dict[str, CharacterUpdate] = Field(default_factory=dict)
    new_known_facts: dict[str, list[KnownFact]] = Field(default_factory=dict)
    new_world_rules: list[WorldRule] = Field(default_factory=list)
    new_glossary: list[GlossaryEntry] = Field(default_factory=list)
    seeds_planted: list[PlannedSeed] = Field(default_factory=list)   # ids minted on commit
    seeds_reinforced: list[str] = Field(default_factory=list)
    seeds_paid: list[str] = Field(default_factory=list)
    # reconciliation signal for the receding-horizon re-planner
    planned_but_absent: list[str] = Field(default_factory=list)
    unplanned_but_present: list[str] = Field(default_factory=list)


ArcMap.model_rebuild()
