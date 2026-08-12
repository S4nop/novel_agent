"""Reviser — bounded revise loop with keep-best (DESIGN §3, invariant #5).

Takes a Draft plus concrete findings and repairs only those findings. The loop
is owned by CODE, not the model: bounded to K iterations, re-checks after each,
and keeps the BEST version seen (never blindly the last — over-editing regresses).

Findings come from the mechanical style lint (Track C) and a length check, so
the revise target is objective rather than vibes.
"""
from __future__ import annotations

from dataclasses import dataclass

from .artifacts import Draft
from .context_pack import ContextPack
from .drafter import extract_fact_requests
from .llm import LLM
from .prompt_store import render
from .style import Violation, lint_prose, style_score

# A draft must land within this band of the target length to pass.
LENGTH_TOLERANCE = 0.15
PASS_SCORE = 85


@dataclass
class RevisionResult:
    draft: Draft
    score: int
    iterations: int
    passed: bool
    remaining: list[Violation]


def length_findings(draft: Draft, target: int) -> list[str]:
    lo, hi = int(target * (1 - LENGTH_TOLERANCE)), int(target * (1 + LENGTH_TOLERANCE))
    if draft.char_count < lo:
        need = target - draft.char_count
        return [
            f"분량 미달: 현재 {draft.char_count}자, 목표 {target}자 (약 {need}자 부족). "
            "반드시 **대사(티키타카)를 늘려서** 채우세요. 지문·설명·묘사를 덧붙여 늘리면 반려입니다."
        ]
    if draft.char_count > hi:
        return [f"분량 초과: 현재 {draft.char_count}자, 목표 {target}자. 늘어지는 지문을 줄이세요."]
    return []


def _fix_instructions(violations: list[Violation], length: list[str]) -> str:
    """Turn findings into directive edits. Resolves the length↔dialogue conflict
    explicitly: padding with narration is what made an earlier revision worse."""
    lines = list(length)
    rules = {v.rule for v in violations}

    if length and ("대사 줄 비중" in rules or "연속 지문 줄" in rules):
        lines.append(
            "★ 분량과 대사 비중을 동시에 해결하는 유일한 방법: 지문 덩어리를 인물 간 "
            "짧은 대화로 바꿔 쓰세요. 서술로 설명한 내용을 인물이 말다툼·흥정·비아냥으로 "
            "주고받게 만들면 분량과 대사 비중이 함께 올라갑니다."
        )
    for v in violations:
        if v.evidence:
            lines.append(f"{v.rule}: 다음 표현을 전부 찾아 삭제하거나 대체 — {v.evidence}")
        else:
            lines.append(f"{v.rule}: 현재 {v.count}, 한도 {v.limit} 이내로 맞추세요")
    return "\n".join(f"- {l}" for l in lines)


def _fitness(draft: Draft, target: int,
             forbidden_terms: list[str] | None = None) -> tuple[int, int]:
    """Ranking key for keep-best. Length compliance is a GATE criterion, so it
    outranks style score: a length-correct draft beats a short one at equal score.
    (Style score alone is blind to this — trading one violation for another keeps
    the number identical while the draft genuinely improves.)"""
    return (0 if length_findings(draft, target) else 1,
            style_score(draft.prose, target_chars=target,
                        forbidden_terms=forbidden_terms))


def revise_draft(
    llm: LLM,
    draft: Draft,
    pack: ContextPack,
    *,
    target_chars: int,
    max_iterations: int = 3,
    # The author's absolute prohibitions (테스트 피드백 1-④). Passed in rather
    # than read here so the lint stays a pure function of its inputs.
    forbidden_terms: list[str] | None = None,
    # Cheap per-iteration continuity rules (Track A's deterministic half),
    # injected so the reviser needs no canon knowledge and stays testable.
    # The expensive LLM continuity judge runs once at the gate, not in here.
    extra_findings=None,
    max_tokens: int = 32768,
) -> RevisionResult:
    """Repair the draft against lint + length findings. Returns the best version seen."""
    best = draft
    best_fit = _fitness(draft, target_chars, forbidden_terms)
    iterations = 0

    for _ in range(max_iterations):
        violations = lint_prose(best.prose, target_chars=target_chars,
                                forbidden_terms=forbidden_terms)
        if extra_findings is not None:
            violations = violations + list(extra_findings(best))
        length = length_findings(best, target_chars)
        if not violations and not length:
            break

        iterations += 1
        prose = llm.text(
            [
                {"role": "system", "content": pack.system},
                {"role": "user", "content": render(
                    "revise_instruction",
                    prefix=pack.cached_prefix, suffix=pack.volatile_suffix,
                    findings=_fix_instructions(violations, length),
                    prose=best.prose)},
            ],
            max_tokens=max_tokens,
        )
        cleaned, requests = extract_fact_requests(prose)
        candidate = Draft(
            episode_number=draft.episode_number,
            prose=cleaned,
            fact_requests=requests or draft.fact_requests,
        )
        # keep-best: an edit that makes things worse is discarded
        fit = _fitness(candidate, target_chars, forbidden_terms)
        if fit > best_fit or (
            fit == best_fit
            and abs(candidate.char_count - target_chars) < abs(best.char_count - target_chars)
        ):
            best, best_fit = candidate, fit

    best_score = style_score(best.prose, target_chars=target_chars,
                             forbidden_terms=forbidden_terms)
    remaining = lint_prose(best.prose, target_chars=target_chars,
                           forbidden_terms=forbidden_terms)
    if extra_findings is not None:
        remaining = remaining + list(extra_findings(best))
    # A continuity blocker is a HARD gate — it cannot be style-scored away.
    passed = (best_score >= PASS_SCORE
              and not length_findings(best, target_chars)
              and not any(v.severity == "blocker" for v in remaining))
    return RevisionResult(
        draft=best, score=best_score, iterations=iterations,
        passed=passed, remaining=remaining,
    )
