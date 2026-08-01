"""Korean web-novel prose style lint — Track C of the review gate (DESIGN §5).

Thresholds are derived from Korean 웹소설 practitioner sources (문피아/노벨피아
연재 커뮤니티, 나무위키 소설 작법/문체, 출판사 편집자 조언). See
`docs/style-spec.md` for the sourced spec.

The point: what readers call 유치함/오글거림 is not a matter of taste — it is
mostly a list of mechanically detectable habits. This module makes them
countable so the gate can block on them instead of vibing.

Pure functions, no LLM, no I/O — unit-testable in milliseconds.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ── vocabulary blacklists (from the researched craft rules) ──────────────────
INTENSIFIERS = ["정말", "너무", "매우", "엄청", "굉장히", "완전히", "무려", "아주", "몹시"]
TRANSLATIONESE = ["것이었다", "인 것이다", "하게 되었다", "에 의해", "에 대해", "로 인해",
                  "라는 것을 알 수 있었다", "하고 있었다"]
EMOTION_NAMING = ["분노했다", "분노가", "슬펐다", "두려웠다", "당황했다", "전율했다",
                  "화가 났다", "기뻤다", "무서웠다", "짜증이 났다"]
SELF_PRAISE = ["잘생긴", "천재", "역시 나", "완벽한 내", "명불허전", "압도적인 내",
               "우람한", "내 미모", "내 실력에"]
LIGHT_NOVEL_INTERJECTIONS = ["오이오이", "흐응", "에엥", "후후", "헤에", "큭", "우와아",
                             "오오옷", "아아아"]
EXPOSITION_TELLS = ["알다시피", "그러니까 말이야", "로 유명한", "그 유명한", "라고 불리는"]
SAGEUK_IN_NARRATION = ["하였다", "이니라", "노라", "이러하다", "하더이다"]
LABEL_WORDS = ["전형적인", "대표적인"]
GRAND_DECLARATION = ["하리라", "운명", "숙명", "각오", "이 몸", "보여주마", "각인시켜"]


@dataclass(frozen=True)
class Violation:
    rule: str
    severity: str          # "blocker" | "major" | "minor"
    count: int
    limit: str
    evidence: str = ""
    limit_num: float = 0.0     # numeric threshold, for magnitude-aware scoring
    lower_is_better: bool = True

    def __str__(self) -> str:
        ev = f" — {self.evidence}" if self.evidence else ""
        return f"[{self.severity}] {self.rule}: {self.count} (한도 {self.limit}){ev}"

    @property
    def overage(self) -> float:
        """How badly the limit is breached, 1.0 = just over. Lets the reviser see
        partial progress: 27 exclamations and 12 are both 'one major', but 12 is
        much better, and keep-best must be able to tell."""
        if not self.lower_is_better:                       # ratio rules (e.g. ≥40%)
            if self.limit_num <= 0:
                return 1.0
            return max(1.0, self.limit_num / max(self.count, 1))
        if self.limit_num > 0:
            return max(1.0, self.count / self.limit_num)
        return max(1.0, float(self.count))                 # limit is 0 → count is the overage


def _lines(text: str) -> list[str]:
    return [l.strip() for l in text.split("\n") if l.strip()]


def is_dialogue(line: str) -> bool:
    return line.lstrip()[:1] in {'"', "“", "‘", "'"}


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?。])\s+|\n+", text)
    return [p.strip() for p in parts if p.strip()]


def _hits(text: str, needles: list[str]) -> list[str]:
    """Substring match — correct for verb-ending patterns (번역체, 사극체, 감정 직설)
    which legitimately follow a Hangul stem."""
    return [n for n in needles if n in text]


def _word_hits(text: str, needles: list[str]) -> list[str]:
    """Standalone-word match for adverbs/interjections/labels.

    Plain substring matching produces false positives across morpheme
    boundaries — e.g. '아주' inside '되찾아주셨다', which is correct Korean.
    Requiring the match not to follow a Hangul syllable fixes it.
    """
    return [n for n in needles
            if re.search(r"(?<![가-힣])" + re.escape(n), text)]


def lint_prose(text: str, *, target_chars: int = 5200) -> list[Violation]:
    """Return every style violation, worst first. Thresholds scale with length."""
    scale = max(1.0, len(text) / target_chars)
    lines = _lines(text)
    dialogue_lines = [l for l in lines if is_dialogue(l)]
    narration = "\n".join(l for l in lines if not is_dialogue(l))
    sentences = split_sentences(text)
    v: list[Violation] = []

    def add(rule, severity, count, limit, evidence="", *, limit_num=0.0,
            lower_is_better=True):
        """Record a violation. Only called when the rule is actually breached —
        ratio rules must call this from inside their own comparison, since a
        legitimate violation can have a count of 0 (e.g. 0% dialogue)."""
        v.append(Violation(rule, severity, count, limit, evidence,
                           limit_num=limit_num, lower_is_better=lower_is_better))

    def add_if(rule, severity, count, limit, evidence=""):
        if count:
            add(rule, severity, count, limit, evidence)

    # Percentage rules are meaningless on a handful of sentences.
    MIN_SAMPLE = 10

    # ── punctuation / amplification ─────────────────────────────────────────
    add_if("겹부호(?!, !!)", "blocker", len(re.findall(r"[!?]{2,}", text)), "0")
    add_if("지문 내 느낌표", "major", narration.count("!"), "0")
    excl = text.count("!")
    if excl > 8 * scale:
        add("느낌표 과다", "major", excl, f"{int(8 * scale)}", limit_num=8 * scale)
    add_if("비표준 말줄임표", "minor",
           len(re.findall(r"(?<!\.)\.\.(?!\.)|···|‥", text)), "0 (…… 사용)")
    add_if("모음/자음 늘여쓰기", "major",
           max(0, len(re.findall(r"([가-힣])\1{2,}", text)) - int(scale)), f"{int(scale)}")
    add_if("물결표", "minor", text.count("~"), "0")

    # ── narration doing the reader's work ──────────────────────────────────
    # Scope matters: intensifiers and self-praise are NARRATION faults — in
    # dialogue "아주 조선시대네" is natural speech, not a style defect.
    for label, needles, sev, scope, matcher in [
        ("강도부사(지문)", INTENSIFIERS, "major", narration, _word_hits),
        ("자기 칭찬 서술", SELF_PRAISE, "blocker", narration, _word_hits),
        ("악역/인물 라벨링", LABEL_WORDS, "major", narration, _word_hits),
        ("라노벨체 감탄사", LIGHT_NOVEL_INTERJECTIONS, "blocker", text, _word_hits),
        ("번역체 종결", TRANSLATIONESE, "major", text, _hits),
        ("감정 직설", EMOTION_NAMING, "major", narration, _hits),
        ("설명충 관용구", EXPOSITION_TELLS, "major", text, _hits),
        ("비장 선언 어휘", GRAND_DECLARATION, "major", text, _hits),
    ]:
        hit = matcher(scope, needles)
        add_if(label, sev, len(hit), "0", ", ".join(hit[:4]))

    hit = _hits(narration, SAGEUK_IN_NARRATION)
    add_if("지문에 사극체(대사에만 허용)", "major", len(hit), "0", ", ".join(hit[:4]))

    # ── rhythm ──────────────────────────────────────────────────────────────
    if len(sentences) >= MIN_SAMPLE:
        avg = sum(len(s) for s in sentences) / len(sentences)
        if avg > 35:
            add("평균 문장 길이", "major", int(avg), "35자", limit_num=35)
        long_ratio = sum(1 for s in sentences if len(s) > 50) / len(sentences)
        if long_ratio > 0.10:
            add("50자 초과 문장 비율", "minor", int(long_ratio * 100), "10%")

    def _max_run(values) -> int:
        run = best = 1 if values else 0
        for a, b in zip(values, values[1:]):
            run = run + 1 if a == b else 1
            best = max(best, run)
        return best

    # Rhythm rules apply to CONTIGUOUS NARRATION BLOCKS only. Dialogue has its
    # own cadence (and naturally ends in 다), and an intervening line of dialogue
    # genuinely breaks the monotony — so runs must not be measured across it.
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if is_dialogue(line):
            if current:
                blocks.append(current)
                current = []
        else:
            current.extend(split_sentences(line))
    if current:
        blocks.append(current)

    # Identical ending form repeated (e.g. 확인했다 / 점검했다 / 계산했다).
    ident = max(
        (_max_run([re.sub(r"[.!?]+$", "", s)[-2:] for s in b if len(s) >= 3])
         for b in blocks),
        default=0,
    )
    if ident >= 3:
        add("동일 종결어미 연속", "major", ident, "2연속", limit_num=2)

    # 다다다체 — an unbroken run of plain declaratives with no variation.
    # Only SUBSTANTIAL sentences count: the sources prescribe staccato short
    # sentences for action beats ("액션 구간은 극단 단문 연타"), so a burst of
    # 2~8자 action lines is correct craft, not monotony.
    worst_plain = 0
    for b in blocks:
        run = 0
        for s in b:
            if len(s) < 15:
                continue
            run = run + 1 if re.search(r"다[.!?]*$", s) else 0
            worst_plain = max(worst_plain, run)
    if worst_plain >= 6:
        add("다다다체(평서 종결 연속)", "major", worst_plain, "5문장", limit_num=5)

    if len(sentences) >= MIN_SAMPLE:
        na = sum(1 for s in sentences if s.startswith("나는"))
        if na / len(sentences) > 0.10:
            add("'나는'으로 시작하는 문장 비율", "minor", int(na / len(sentences) * 100), "10%")

    # ── dialogue / narration balance (mobile readability) ───────────────────
    if len(lines) >= MIN_SAMPLE:
        ratio = len(dialogue_lines) / len(lines)
        if ratio < 0.40:
            add("대사 줄 비중", "major", int(ratio * 100), "40% 이상",
                limit_num=40, lower_is_better=False)

    # A narration WALL is measured in characters, not lines — a run of short
    # action beats reads fast, while 300+자 of unbroken prose is the brick that
    # drives mobile readers away.
    run = worst = 0
    for l in lines:
        run = 0 if is_dialogue(l) else run + len(l)
        worst = max(worst, run)
    if worst > 300:
        add("연속 지문 분량(대사 없이)", "major", worst, "300자", limit_num=300)

    long_paras = sum(1 for l in lines if len(l) > 120)
    add_if("벽돌 문단(120자 초과)", "minor", long_paras, "0")

    long_dlg = sum(1 for l in dialogue_lines if len(l) > 60)
    add_if("40자 초과 장문 대사", "minor", long_dlg, "0")

    order = {"blocker": 0, "major": 1, "minor": 2}
    return sorted(v, key=lambda x: (order[x.severity], -x.count))


def style_score(text: str, *, target_chars: int = 5200) -> int:
    """0-100. blockers cost 12, majors 5, minors 2 — scaled by how far over the
    limit each violation is (capped at 2x) so partial fixes are visible to the
    reviser's keep-best comparison."""
    weight = {"blocker": 12, "major": 5, "minor": 2}
    total = sum(
        weight[x.severity] * min(2.0, 0.5 + 0.5 * x.overage)
        for x in lint_prose(text, target_chars=target_chars)
    )
    return max(0, round(100 - total))
