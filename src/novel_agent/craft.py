"""Track B — craft judging against a genre rubric (DESIGN §3, 클로드 제안 5).

Track A asks "is this true?" and hard-blocks. Track B asks "is this any good?"
and cannot, because craft is a judgement, not a fact. A subjective judge with
blocking power stops an unattended run on an opinion — so every finding here is
capped at `major`, feeds the reviser and the human gate, and never refuses a
commit on its own.

Three dimensions, deliberately narrow:
  plot       — causality, motivation, contrivance, forced 떡밥 payoff
  character  — drift from the locked VoiceBible and per-character voice cards
  genre      — does the 사이다 land, does the 절단 pull to the next episode

It never sees the beat sheet's intent (invariant #3, context-level judge
independence): shown what the episode was *trying* to do, a judge grades the
attempt instead of the result — which is exactly what a reader cannot see.

Findings are `style.Violation` so they flow through the existing gate, the
reviser's fix instructions, and the API/UI unchanged.
"""
from __future__ import annotations

from .artifacts import Canon, Draft, GenreProfile, VoiceBible
from .llm import LLM
from .prompt_store import render
from .schemas import CraftReportDraft, OpeningEndingReportDraft
from .style import Violation

RULE_PLOT = "크래프트: 플롯 논리"
RULE_CHARACTER = "크래프트: 캐릭터 일관성"
RULE_GENRE = "크래프트: 장르 기대"

_DIMENSION_RULES = {"plot": RULE_PLOT, "character": RULE_CHARACTER, "genre": RULE_GENRE}


def _genre_expectations(profile: GenreProfile) -> str:
    bits = [
        f"- 장르: {profile.sub_genre} · 독자: {profile.audience}",
        f"- 사이다는 최소 {profile.target_catharsis_cadence}화마다 터져야 합니다.",
        f"- 연속 고구마는 최대 {profile.max_consecutive_frustration_beats}회까지입니다.",
    ]
    if profile.trope_checklist:
        bits.append("- 기대 트로프: " + ", ".join(profile.trope_checklist))
    if profile.forbidden_anti_patterns:
        bits.append("- 금지: " + ", ".join(profile.forbidden_anti_patterns))
    return "\n".join(bits)


def _voices(canon: Canon, voice: VoiceBible) -> str:
    lines = [f"[작품 문체] {voice.spec}"] if voice.spec else []
    for name, c in canon.characters.items():
        v = c.voice
        parts = [f"- {name}"]
        if v.speech_register:
            parts.append(f"말투={v.speech_register}")
        if v.honorific_pattern:
            parts.append(f"존대={v.honorific_pattern}")
        if v.speech_tics:
            parts.append(f"버릇={', '.join(v.speech_tics)}")
        lines.append(" · ".join(parts))
        if v.exemplar_lines:
            lines.append(f'    예: "{v.exemplar_lines[0]}"')
    return "\n".join(lines) or "- 없음"


def judge_craft(
    llm: LLM,
    draft: Draft,
    profile: GenreProfile,
    canon: Canon,
    voice: VoiceBible,
    *,
    max_findings: int = 8,
) -> list[Violation]:
    """Rubric-scored craft findings. Never blocking, never fatal.

    A judge failure returns no findings rather than raising: craft is advisory,
    and an unattended run must not die because an opinion call timed out.
    """
    try:
        report = llm.structured(
            [
                {"role": "system", "content": render("craft_system")},
                {"role": "user", "content": render(
                    "craft_check",
                    genre=_genre_expectations(profile),
                    voices=_voices(canon, voice),
                    episode_number=draft.episode_number,
                    prose=draft.prose)},
            ],
            CraftReportDraft,
        )
    except Exception:  # noqa: BLE001 — advisory track
        return []

    out: list[Violation] = []
    for f in (report.findings or [])[:max_findings]:
        if not (f.problem and f.evidence):
            continue           # a craft claim without evidence is an opinion
        rule = _DIMENSION_RULES.get((f.dimension or "").strip().lower())
        if rule is None:
            continue
        # capped at major on purpose — see the module docstring
        severity = "minor" if (f.severity or "").strip().lower() == "minor" else "major"
        out.append(Violation(
            rule=rule, severity=severity, count=1, limit="0건", limit_num=0,
            evidence=f"{f.problem} — 근거: {f.evidence}"))
    order = {"major": 0, "minor": 1}
    return sorted(out, key=lambda v: order[v.severity])


# ── hook and 절단 — the two points 연독률 actually turns on ────────────────────
# These are NOT taste. "Does the episode end on an unresolved question" is a
# structural property of the format, and it is the mechanic that sells the next
# episode. So unlike the rest of Track B these carry gate weight: the prompt has
# asked for a strong 훅/절단 since day one and nothing ever checked, which is how
# an episode shipped ending on a character boarding a transport and a door
# closing — a closing shot, not a cliffhanger.
RULE_HOOK = "도입 훅 약함"
RULE_CLIFFHANGER = "절단 실패(다음 화를 안 봐도 됨)"

_PART_RULES = {"hook": RULE_HOOK, "cliffhanger": RULE_CLIFFHANGER}

# Only the two ends are judged, so the call stays small and the judge cannot be
# distracted by the middle.
_EDGE_CHARS = 900


def judge_opening_and_ending(llm: LLM, draft: Draft) -> list[Violation]:
    """Structural check on the first and last passages. Findings are `major`
    and count toward the gate (see reviser._structure_findings)."""
    prose = draft.prose.strip()
    if len(prose) < 200:
        return []
    try:
        report = llm.structured(
            [
                {"role": "system", "content": render("opening_ending_system")},
                {"role": "user", "content": render(
                    "opening_ending_check",
                    episode_number=draft.episode_number,
                    opening=prose[:_EDGE_CHARS],
                    ending=prose[-_EDGE_CHARS:])},
            ],
            OpeningEndingReportDraft,
        )
    except Exception:  # noqa: BLE001 — advisory on failure, never fatal
        return []

    out: list[Violation] = []
    for f in report.findings or []:
        rule = _PART_RULES.get((f.part or "").strip().lower())
        if rule is None or not (f.problem and f.evidence):
            continue
        out.append(Violation(
            rule=rule, severity="major", count=1, limit="0건", limit_num=0,
            evidence=f"{f.problem} — 근거: {f.evidence}"))
    return out
