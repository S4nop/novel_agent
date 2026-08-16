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

    # Direct the length fix by WHICH way the balance is off. Emitting the
    # add-dialogue advice unconditionally is what ratcheted one draft to 83%
    # dialogue: every short draft was told to convert narration into talk, and
    # no finding ever asked for the opposite.
    if length and "지문 부족(대사 과다)" in rules:
        lines.append(
            "★ 분량은 대사로 채우지 마세요. 이미 대사 비중이 과합니다. 인물의 행동, "
            "공간과 감각 묘사, 짧은 속내로 채우고, 티키타카가 길게 이어지는 구간은 "
            "오히려 줄이세요."
        )
    elif length and ("대사 줄 비중" in rules or "연속 지문 줄" in rules):
        lines.append(
            "★ 분량과 대사 비중을 동시에 해결하는 유일한 방법: 지문 덩어리를 인물 간 "
            "짧은 대화로 바꿔 쓰세요. 서술로 설명한 내용을 인물이 말다툼·흥정·비아냥으로 "
            "주고받게 만들면 분량과 대사 비중이 함께 올라갑니다."
        )
    for v in violations:
        # RULE_INFO carries a concrete method for every rule. It was written for
        # the author-facing UI (테스트 피드백 3) and never reached the model that
        # actually has to perform the fix, which got only "현재 7, 한도 5문장" —
        # a bare number, the exact complaint that feedback was about. Measured:
        # three clean-store attempts failed on the same rhythm rules with scores
        # going 78 -> 79 -> 74, i.e. the loop was not converging.
        how = v.meta.fix if v.meta else ""
        if v.evidence:
            lines.append(f"{v.rule}: 다음 표현을 전부 찾아 삭제하거나 대체 — {v.evidence}"
                         + (f" ({how})" if how else ""))
        else:
            lines.append(f"{v.rule}: 현재 {v.count}, 한도 {v.limit} 이내로 맞추세요."
                         + (f" 방법: {how}" if how else ""))
    return "\n".join(f"- {l}" for l in lines)


# Structural faults, as opposed to stylistic nits: the gate treats these like
# length. Hook and 절단 are here because they are the mechanic that sells the
# next episode, not a matter of taste.
_STRUCTURAL_RULES = ("지문 부족(대사 과다)", "대사 줄 비중",
                     "도입 훅 약함", "절단 실패(다음 화를 안 봐도 됨)",
                     "다음 화로 이어질 압력 없음")


def _balance_findings(draft: Draft, target: int,
                      forbidden_terms: list[str] | None = None,
                      extra: list[Violation] | None = None) -> list[Violation]:
    """Structural failures that must not be traded away for a better style score."""
    found = [v for v in lint_prose(draft.prose, target_chars=target,
                                   forbidden_terms=forbidden_terms)
             if v.rule in _STRUCTURAL_RULES]
    found += [v for v in (extra or []) if v.rule in _STRUCTURAL_RULES]
    return found


def _fitness(draft: Draft, target: int,
             forbidden_terms: list[str] | None = None,
             extra_findings=None,
             structural: list[Violation] | None = None) -> tuple[int, int, int]:
    """Ranking key for keep-best, most significant criterion first.

    Continuity outranks everything: a canon break is a hard gate failure, so a
    candidate that fixes one is better even at a worse style score. Length is
    next (also a gate criterion) — a length-correct draft beats a short one at
    equal score. Style score alone is blind to both: trading one violation for
    another keeps the number identical while the draft genuinely improves.
    """
    blockers = 0
    if extra_findings is not None:
        blockers = sum(1 for v in extra_findings(draft) if v.severity == "blocker")
    # Length and dialogue balance are BOTH gate criteria, counted together so a
    # draft satisfying both outranks one satisfying either. Balance has to sit at
    # this tier, not inside the style score: length compliance is worth a whole
    # tier, so any style-level penalty loses to it and the loop learns to hit
    # 분량 by piling on dialogue — measured at 66-83% before this.
    gates = ((0 if length_findings(draft, target) else 1)
             + (0 if _balance_findings(draft, target, forbidden_terms, structural) else 1))
    return (-blockers, gates,
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
    # Structural findings judged once on the incoming draft (hook / 절단).
    # Not recomputed per iteration: that would cost an LLM call each pass.
    structural_findings: list[Violation] | None = None,
    max_tokens: int = 32768,
) -> RevisionResult:
    """Repair the draft against lint + length findings. Returns the best version seen."""
    best = draft
    best_fit = _fitness(draft, target_chars, forbidden_terms, extra_findings,
                        structural_findings)
    iterations = 0

    for _ in range(max_iterations):
        violations = lint_prose(best.prose, target_chars=target_chars,
                                forbidden_terms=forbidden_terms)
        if extra_findings is not None:
            violations = violations + list(extra_findings(best))
        if structural_findings:
            violations = violations + list(structural_findings)
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
        fit = _fitness(candidate, target_chars, forbidden_terms, extra_findings,
                       structural_findings)
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
    if structural_findings:
        remaining = remaining + list(structural_findings)
    # A continuity blocker is a HARD gate — it cannot be style-scored away.
    passed = (best_score >= PASS_SCORE
              and not length_findings(best, target_chars)
              and not _balance_findings(best, target_chars, forbidden_terms,
                                        structural_findings)
              and not any(v.severity == "blocker" for v in remaining))
    return RevisionResult(
        draft=best, score=best_score, iterations=iterations,
        passed=passed, remaining=remaining,
    )
