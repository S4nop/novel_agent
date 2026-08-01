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


def _fitness(draft: Draft, target: int) -> tuple[int, int]:
    """Ranking key for keep-best. Length compliance is a GATE criterion, so it
    outranks style score: a length-correct draft beats a short one at equal score.
    (Style score alone is blind to this — trading one violation for another keeps
    the number identical while the draft genuinely improves.)"""
    return (0 if length_findings(draft, target) else 1,
            style_score(draft.prose, target_chars=target))


def revise_draft(
    llm: LLM,
    draft: Draft,
    pack: ContextPack,
    *,
    target_chars: int,
    max_iterations: int = 3,
    max_tokens: int = 32768,
) -> RevisionResult:
    """Repair the draft against lint + length findings. Returns the best version seen."""
    best = draft
    best_fit = _fitness(draft, target_chars)
    iterations = 0

    for _ in range(max_iterations):
        violations = lint_prose(best.prose, target_chars=target_chars)
        length = length_findings(best, target_chars)
        if not violations and not length:
            break

        iterations += 1
        prose = llm.text(
            [
                {"role": "system", "content": pack.system},
                {
                    "role": "user",
                    "content": (
                        f"{pack.cached_prefix}\n\n{pack.volatile_suffix}\n\n"
                        "[아래 원고를 고쳐 다시 쓰세요]\n"
                        "지적된 문제만 고치고, 잘 된 부분과 사건 전개는 그대로 유지하세요.\n"
                        "장면을 삭제해 분량을 줄이지 마세요.\n\n"
                        f"[지적 사항]\n{_fix_instructions(violations, length)}\n\n"
                        "[원고]\n" + best.prose + "\n\n"
                        "고친 원고 전문만 출력하세요. 설명이나 머리말을 붙이지 마세요."
                    ),
                },
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
        fit = _fitness(candidate, target_chars)
        if fit > best_fit or (
            fit == best_fit
            and abs(candidate.char_count - target_chars) < abs(best.char_count - target_chars)
        ):
            best, best_fit = candidate, fit

    best_score = style_score(best.prose, target_chars=target_chars)
    remaining = lint_prose(best.prose, target_chars=target_chars)
    passed = best_score >= PASS_SCORE and not length_findings(best, target_chars)
    return RevisionResult(
        draft=best, score=best_score, iterations=iterations,
        passed=passed, remaining=remaining,
    )
