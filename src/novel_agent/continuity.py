"""Track A — continuity checking against canon (DESIGN §3, §5).

WHY THIS EXISTS, in the project's own measured words (`phase0-results.md`):
*neither model self-reported its invented facts.* The Drafter's `[[FACT: …]]`
self-reporting is therefore not a safety net — a model that invents a fact
does not raise its hand about it. Continuity has to be checked by something
other than the writer. That makes this module load-bearing for unattended
runs, not a nice-to-have (클로드 제안 5).

Two halves, deliberately:

  1. `deterministic_findings()` — no LLM. Exact, instant, unit-testable, and
     unfoolable: a dead character speaking, a glossary term spelled a new way,
     a planned entity missing. These are the checks that must never depend on
     a model's mood.
  2. `check_continuity()` — the LLM claim-check for everything a rule cannot
     see (invented relatives, contradicted backstory, a power the character
     does not have). Runs BLIND: it receives canon and prose only, never the
     beat sheet's intent or the author's rationale, so it cannot be talked
     into agreeing with the draft (invariant #3, context-level independence).

Findings are `style.Violation` so they flow through the existing gate, the
reviser's fix instructions, and the API/UI rendering unchanged — one findings
vocabulary across tracks rather than a parallel one (invariant #4).
"""
from __future__ import annotations

import re

from .artifacts import BeatSheet, Canon, Draft, Summary
from .llm import LLM
from .prompts import render
from .schemas import ContinuityReportDraft
from .style import Violation

# Statuses that mean "must not act in a scene". Anything else is treated as
# present-and-able, so an unfamiliar status never silently suppresses the check.
_ABSENT_STATUSES = {"dead", "사망", "죽음", "retired", "퇴장", "이탈", "실종"}

RULE_RETIRED_ACTOR = "캐논 위반: 퇴장한 인물 등장"
RULE_GLOSSARY_DRIFT = "캐논 위반: 용어 표기 흔들림"
RULE_PLANNED_ABSENT = "계획 이탈: 예정 인물 부재"
RULE_CANON_CONTRADICTION = "캐논 위반: 사실 모순"
# The story contradicting itself, where no canon fact is involved: an earlier
# episode says one thing, or this episode says two. A canon-only check could
# never see either — a live 4화 committed with "같은 날 같은 처리 번호" next to
# "석 달째 일을 못 나갔다".
RULE_STORY_CONTRADICTION = "앞뒤 모순: 앞선 화와 충돌"
RULE_INTERNAL_CONTRADICTION = "앞뒤 모순: 이 화 안에서 충돌"
_KIND_RULES = {"canon": RULE_CANON_CONTRADICTION, "earlier": RULE_STORY_CONTRADICTION,
               "internal": RULE_INTERNAL_CONTRADICTION}


# A canonical_form is the exact string to write. Models keep returning it with a
# parenthetical gloss — "차대성 (관내 호칭: 차 서기)" — which no prose can ever
# match, so the rule fired on the protagonist's own name in every episode. A
# check that misfires every time trains the author to ignore the gate, so the
# annotation is stripped rather than trusted.
def _canonical_spelling(canonical_form: str) -> str:
    return re.sub(r"\s*[(（].*", "", canonical_form or "").strip()


def _mentions(prose: str, card_name: str, aliases: list[str]) -> str | None:
    for name in [card_name, *aliases]:
        if name and name in prose:
            return name
    return None


def deterministic_findings(draft: Draft, beats: BeatSheet, canon: Canon) -> list[Violation]:
    """Exact continuity checks. No LLM, no I/O — milliseconds.

    Intentionally narrow: every rule here is one a model could not argue with.
    Fuzzy judgement belongs in the LLM pass.
    """
    out: list[Violation] = []
    prose = draft.prose

    # 1) a character canon says is gone, acting in the scene
    for name, card in canon.characters.items():
        if card.status.strip().lower() in _ABSENT_STATUSES:
            hit = _mentions(prose, name, card.aliases)
            if hit:
                out.append(Violation(
                    rule=RULE_RETIRED_ACTOR, severity="blocker", count=prose.count(hit),
                    limit="0회", limit_num=0,
                    evidence=f"'{hit}' — 캐논 상태 '{card.status}'인데 본문에 등장"))

    # 2) glossary drift: the term is used, but not in its canonical spelling.
    #    Reader-visible inconsistency and the classic long-serial rot.
    for g in canon.glossary:
        spelling = _canonical_spelling(g.canonical_form)
        if g.term and spelling and g.term != spelling:
            if g.term in prose and spelling not in prose:
                out.append(Violation(
                    rule=RULE_GLOSSARY_DRIFT, severity="major", count=prose.count(g.term),
                    limit="0회", limit_num=0,
                    evidence=f"'{g.term}' → 정본 표기 '{spelling}'"))

    # 3) the plan said this entity would appear and it did not. A reconciliation
    #    signal for the re-planner, not necessarily an error — hence minor.
    absent = [e for e in beats.entities_present if e and e not in prose]
    if absent:
        out.append(Violation(
            rule=RULE_PLANNED_ABSENT, severity="minor", count=len(absent),
            limit="0명", limit_num=0,
            evidence=", ".join(absent[:5])))
    return out


def _canon_digest(canon: Canon) -> str:
    """What the judge is allowed to know: canon facts only, no draft intent."""
    lines: list[str] = []
    for name, c in canon.characters.items():
        bits = [f"- {name}"]
        if c.aliases:
            bits.append(f"(별칭 {', '.join(c.aliases)})")
        if c.immutable_descriptors:
            bits.append("· " + ", ".join(c.immutable_descriptors))
        for label, value in (("상태", c.status), ("위치", c.current_location),
                             ("컨디션", c.condition), ("능력", c.power_level)):
            if value:
                bits.append(f"· {label}={value}")
        lines.append(" ".join(bits))
        for f in c.known_facts:
            lines.append(f"    · {f.fact} ({f.learned_episode}화 시점)")
    rules = [f"- {r.text}" for r in canon.world_rules if r.hard]
    gloss = [f"- {g.canonical_form}" + (f" ({g.notes})" if g.notes else "")
             for g in canon.glossary]
    return "\n".join([
        "[인물]", *(lines or ["- 없음"]),
        "", "[불변 규칙]", *(rules or ["- 없음"]),
        "", "[용어 정본]", *(gloss or ["- 없음"]),
    ])


def _severity(raw: str) -> str:
    """blocker, or major. The author's rule (2026-10-18): anything where the
    story does not hold together is at least major — a live 4화 committed with
    a contradiction the judge had filed as minor, which neither blocked nor
    reached the reviser."""
    return "blocker" if (raw or "").strip().lower() == "blocker" else "major"


def check_continuity(
    llm: LLM,
    draft: Draft,
    beats: BeatSheet,
    canon: Canon,
    *,
    summary: Summary | None = None,
    max_findings: int = 12,
) -> list[Violation]:
    """Deterministic rules + a blind LLM claim-check. Returns every finding.

    A model failure here must not take the episode down with it: continuity is
    a gate input, and an unattended run that crashes on a flaky judge call is
    worse than one that proceeds with the deterministic findings alone.
    """
    findings = deterministic_findings(draft, beats, canon)

    try:
        report = llm.structured(
            [
                {"role": "system", "content": render("continuity_system")},
                {"role": "user", "content": render(
                    "continuity_check",
                    canon=_canon_digest(canon),
                    story_so_far=(summary.story_so_far if summary and summary.story_so_far
                                  else "없음 — 첫 화이거나 기록이 없습니다"),
                    episode_number=draft.episode_number,
                    prose=draft.prose)},
            ],
            ContinuityReportDraft,
        )
    except Exception:  # noqa: BLE001 — judge is advisory; rules already ran
        return findings

    for f in (report.findings or [])[:max_findings]:
        if not (f.canon_fact and f.prose_claim):
            continue
        kind = (f.kind or "canon").strip().lower()
        label = {"earlier": "앞선 화", "internal": "앞부분"}.get(kind, "캐논")
        findings.append(Violation(
            rule=_KIND_RULES.get(kind, RULE_CANON_CONTRADICTION),
            severity=_severity(f.severity), count=1, limit="0건", limit_num=0,
            evidence=f"{label} '{f.canon_fact}' ↔ 본문 '{f.prose_claim}'"
                     + (f" ({f.where})" if f.where else "")))

    order = {"blocker": 0, "major": 1, "minor": 2}
    return sorted(findings, key=lambda v: order[v.severity])


def judged_contradictions(findings: list[Violation]) -> list[Violation]:
    """The judge's findings only. The deterministic rules already run inside
    the revise loop on every candidate, so passing them again would repeat
    them in the rewrite instructions."""
    judged = set(_KIND_RULES.values())
    return [v for v in findings if v.rule in judged]


def blocks_acceptance(findings: list[Violation]) -> bool:
    """Track A is a HARD gate (DESIGN §3): a contradiction cannot be
    style-scored away, so acceptance is refused regardless of the prose score.

    Major blocks too. That is only fair because the reviser now gets every
    contradiction found in the first draft and the verdict is taken on the
    final one — what is still standing had its chance to be fixed. Minor
    (plan versus result) is a note, not a contradiction."""
    return any(v.severity in ("blocker", "major") for v in findings)
