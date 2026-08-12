"""ContextPackBuilder — deterministic, no LLM (DESIGN §1, §3).

Assembles the context for drafting ONE episode within a fixed token budget:

  • cache-stable PREFIX — byte-stable for the whole run: built ONLY from the
    stable spine (system + NorthStar hard rules + locked VoiceBible + main-cast
    IMMUTABLE descriptors/voice + glossary + genre rubric). Evolving state never
    enters it, so the prompt cache keeps hitting. On Anthropic this prefix is
    sent as the system block carrying the one `cache_control` breakpoint
    (llm.AnthropicLLM._system) — caching is a PREFIX match, so a single
    non-deterministic byte here silently voids the discount for the whole run.
    Watch Usage.cache_hit_rate: a persistent 0.0 means the prefix drifted.
  • volatile SUFFIX — rewritten per episode: Summary, current-status Canon
    slice, K=1 previous episode verbatim, BeatSheet, due foreshadows, and the
    RhythmState pacing directive.

Episode 1 builds with an empty Summary and no previous episode — deterministic
omission, not an error (invariant #4).

Token counting is injected. The default is a char-count stand-in; production
injects the provider's count_tokens (Korean ≈ 1.2–1.5 tok/char, and the ratio
differs per tokenizer — budgets must be re-baselined on real text after any
model switch, DESIGN §5).
"""
from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel

from .artifacts import (
    BeatSheet,
    Canon,
    ContentRating,
    EpisodeRecord,
    GenreProfile,
    NorthStar,
    Summary,
    VoiceBible,
)
from .ledgers import ForeshadowLedger, RhythmState
from .prompts import drafting_system_prompt

TokenCounter = Callable[[str], int]


def _char_count(text: str) -> int:
    """Deterministic stand-in for a real tokenizer (see module docstring)."""
    return len(text)


class TokenBudget(BaseModel):
    prefix_max: int = 18_000
    suffix_max: int = 52_000
    total_max: int = 70_000


class ContextPack(BaseModel):
    system: str
    cached_prefix: str
    volatile_suffix: str
    prefix_tokens: int
    suffix_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.prefix_tokens + self.suffix_tokens

    def to_messages(self) -> list[dict]:
        # System + stable prefix go first (and unchanged) so the provider's
        # automatic prefix cache hits; the volatile suffix is the user turn.
        return [
            {"role": "system", "content": f"{self.system}\n\n{self.cached_prefix}"},
            {"role": "user", "content": self.volatile_suffix},
        ]


class ContextPackBuilder:
    def __init__(
        self,
        token_counter: TokenCounter = _char_count,
        budget: TokenBudget | None = None,
    ) -> None:
        self.count = token_counter
        self.budget = budget or TokenBudget()

    def build(
        self,
        *,
        genre_profile: GenreProfile,
        north_star: NorthStar,
        voice_bible: VoiceBible,
        canon: Canon,
        beat_sheet: BeatSheet,
        foreshadow: ForeshadowLedger,
        rhythm: RhythmState,
        summary: Summary,
        current_episode: int,
        previous_episode: EpisodeRecord | None,
    ) -> ContextPack:
        system = drafting_system_prompt(genre_profile)
        prefix = self._prefix(genre_profile, north_star, voice_bible, canon)
        suffix = self._suffix(
            canon, beat_sheet, foreshadow, rhythm, summary,
            current_episode, previous_episode,
        )
        suffix = self._fit_suffix(suffix, summary, previous_episode)
        return ContextPack(
            system=system,
            cached_prefix=prefix,
            volatile_suffix=suffix,
            prefix_tokens=self.count(prefix),
            suffix_tokens=self.count(suffix),
        )

    # ── cache-stable prefix ────────────────────────────────────────────────────
    def _prefix(
        self,
        gp: GenreProfile,
        ns: NorthStar,
        vb: VoiceBible,
        canon: Canon,
    ) -> str:
        blocks: list[str] = ["# 불변 설정 (캐시 고정)"]

        blocks.append(
            "## 노스스타\n"
            f"전제: {ns.premise}\n"
            f"핵심 갈등: {ns.core_conflict}\n"
            f"주인공의 강점: {ns.protagonist_edge}\n"
            f"에피소드 엔진: {ns.episode_engine}\n"
            f"파워 체계: {ns.power_system}"
        )
        if ns.hard_rules:
            blocks.append("## 절대 규칙\n" + "\n".join(f"- {r}" for r in ns.hard_rules))

        rubric = [
            "## 장르 루브릭",
            f"- 트로프 체크리스트: {', '.join(gp.trope_checklist) or '없음'}",
            f"- 목표 사이다 주기: {gp.target_catharsis_cadence}화 이내",
            f"- 최대 연속 고구마: {gp.max_consecutive_frustration_beats}회",
        ]
        if gp.forbidden_anti_patterns:
            rubric.append("- 금지 패턴: " + ", ".join(gp.forbidden_anti_patterns))
        blocks.append("\n".join(rubric))

        if vb.spec or vb.exemplar_passages:
            vb_block = ["## 보이스 바이블", vb.spec]
            for i, ex in enumerate(vb.exemplar_passages, 1):
                vb_block.append(f"[예문 {i}] {ex}")
            blocks.append("\n".join(b for b in vb_block if b))

        # main-cast IMMUTABLE descriptors + voice only — never evolving state
        for card in canon.main_cast():
            lines = [f"## 주요 인물 — {card.name}"]
            if card.immutable_descriptors:
                lines.append("불변 특징: " + ", ".join(card.immutable_descriptors))
            v = card.voice
            if v.speech_register or v.honorific_pattern:
                lines.append(f"말투: {v.speech_register} / {v.honorific_pattern}")
            if v.speech_tics:
                lines.append("말버릇: " + ", ".join(v.speech_tics))
            for ex in v.exemplar_lines:
                lines.append(f'대사 예시: "{ex}"')
            blocks.append("\n".join(lines))

        if canon.glossary:
            gl = ["## 용어집(고유명사 표기)"]
            gl += [f"- {g.term} → {g.canonical_form}" for g in canon.glossary]
            blocks.append("\n".join(gl))

        return "\n\n".join(blocks)

    # ── volatile suffix ────────────────────────────────────────────────────────
    def _suffix(
        self,
        canon: Canon,
        bs: BeatSheet,
        foreshadow: ForeshadowLedger,
        rhythm: RhythmState,
        summary: Summary,
        current_episode: int,
        previous_episode: EpisodeRecord | None,
    ) -> str:
        blocks: list[str] = [f"# 이번 화: {current_episode}화 집필"]

        if summary.story_so_far or summary.current_arc:
            s = ["## 지금까지의 이야기"]
            if summary.story_so_far:
                s.append(summary.story_so_far)
            if summary.current_arc:
                s.append(f"[현재 아크] {summary.current_arc}")
            blocks.append("\n".join(s))

        status = ["## 현재 상태(주요 인물)"]
        for card in canon.main_cast():
            status.append(
                f"- {card.name}: 상태 {card.status} · 위치 {card.current_location or '미상'} "
                f"· 컨디션 {card.condition or '정상'} · 힘 {card.power_level or '미상'}"
            )
        if len(status) > 1:
            blocks.append("\n".join(status))

        if previous_episode is not None:
            blocks.append(f"## 직전 {previous_episode.episode_number}화 본문\n{previous_episode.prose}")

        blocks.append(self._render_beatsheet(bs))

        due = foreshadow.due(current_episode)
        if due:
            fl = ["## 이번 화에서 회수해야 할 떡밥"]
            fl += [f"- ({s.seed_id}) {s.description} → {s.intended_payoff or '회수'}" for s in due]
            blocks.append("\n".join(fl))

        blocks.append(rhythm.pacing_directive())
        return "\n\n".join(blocks)

    def _render_beatsheet(self, bs: BeatSheet) -> str:
        lines = [
            "## 이번 화 비트시트",
            f"도입 훅: {bs.opening_hook}",
            f"핵심 진전(단 하나): {bs.the_one_progression}",
        ]
        for i, beat in enumerate(bs.beats, 1):
            lines.append(f"{i}. [{beat.beat_type.value}] {beat.text}")
        if bs.seeds_to_pay:
            lines.append("회수할 떡밥: " + ", ".join(bs.seeds_to_pay))
        lines.append(f"마무리 절단: {bs.closing_cliffhanger}")
        return "\n".join(lines)

    # ── budget fitting ─────────────────────────────────────────────────────────
    def _fit_suffix(
        self,
        suffix: str,
        summary: Summary,
        previous_episode: EpisodeRecord | None,
    ) -> str:
        """Guarantee suffix ≤ budget. Trim in priority order: the previous-episode
        verbatim block first (least load-bearing at the tail), then story-so-far.
        The cache-stable prefix is never touched."""
        cap = min(self.budget.suffix_max, self.budget.total_max - self.budget.prefix_max)
        if self.count(suffix) <= cap:
            return suffix

        # 1) hard-truncate the previous-episode verbatim block to its tail
        if previous_episode is not None and previous_episode.prose:
            marker = f"## 직전 {previous_episode.episode_number}화 본문\n"
            idx = suffix.find(marker)
            if idx != -1:
                over = self.count(suffix) - cap
                prose = previous_episode.prose
                keep = max(0, len(prose) - over - len("\n…(중략)…\n"))
                trimmed = ("…(중략)…\n" + prose[-keep:]) if keep else "…(생략)…"
                suffix = suffix[: idx + len(marker)] + trimmed + \
                    suffix[idx + len(marker) + len(prose):]
        if self.count(suffix) <= cap:
            return suffix

        # 2) last resort: hard cut the whole suffix to the cap
        return suffix[:cap]
