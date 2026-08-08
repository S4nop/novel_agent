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


# ── rule metadata ────────────────────────────────────────────────────────────
# The "why" was only in docs/style-spec.md, i.e. developer-facing. Without it a
# score reads as an arbitrary deduction and teaches the author nothing, so each
# rule now carries its rationale and a concrete fix to the UI and the API.
@dataclass(frozen=True)
class RuleMeta:
    why: str
    fix: str
    bad: str = ""
    good: str = ""


RULE_INFO: dict[str, RuleMeta] = {
    "겹부호(?!, !!)": RuleMeta(
        "한글 맞춤법에 없는 표기입니다. 부호로 감정을 대신하는 순간 초보 원고로 읽힙니다.",
        "겹부호를 지우고, 놀람은 짧은 단문과 행동 한 줄로 표현하세요.",
        '"뭐야?! 저놈이 훔쳤다고?!!"', '"저놈이 훔쳤다."\n손끝이 저렸다.'),
    "지문 내 느낌표": RuleMeta(
        "지문의 느낌표는 서술자가 대신 흥분하는 인상을 줍니다. 독자가 느낄 자리를 뺏습니다.",
        "지문에서는 느낌표를 모두 빼고, 필요하면 대사로 옮기세요."),
    "느낌표 과다": RuleMeta(
        "느낌표가 흔해지면 정작 중요한 장면에서 쓸 강조 수단이 남지 않습니다.",
        "화당 8개 이하로 줄이고, 남길 것은 가장 센 한두 장면에만 두세요."),
    "모음/자음 늘여쓰기": RuleMeta(
        "'크아아아앙' 같은 늘여쓰기는 라노벨·번역투 신호로 읽혀 남성향 독자가 이탈합니다.",
        "소리 대신 '결과'를 쓰세요. 예: 기왓장이 튀었다."),
    "물결표": RuleMeta("물결표는 웹소설 지문에서 가벼운 인상을 줍니다.", "삭제하세요."),
    "비표준 말줄임표": RuleMeta(
        "'...' 대신 '……'가 표준 표기입니다. 표기 흔들림은 아마추어 신호입니다.",
        "'……' 한 형태로 통일하고 화당 6개 이하로 제한하세요."),
    "강도부사(지문)": RuleMeta(
        "'정말·너무·아주' 같은 강도부사는 문장을 늘리고 주술을 흐립니다. 미사여구는 유치함의 원료입니다.",
        "부사를 지우고 동사·구체적 수치로 대체하세요.",
        "정말 엄청나게 거대한 문이 천천히 열렸다.", "강철 문이 열렸다. 어른 키 세 배였다."),
    "번역체 종결": RuleMeta(
        "'~것이었다/~하게 되었다'류는 번역투로 읽히며 문장을 수동적으로 만듭니다.",
        "문장을 동사로 끝내고 능동으로 되돌리세요."),
    "감정 직설": RuleMeta(
        "감정을 이름으로 부르면 독자가 그 감정에 들어가지 못합니다. 설명당한 감정은 느껴지지 않습니다.",
        "신체 반응 하나 또는 행동 하나로 대체하세요.",
        "나는 너무 분노했다.", "어금니에서 소리가 났다. 나는 명패를 반으로 접었다."),
    "자기 칭찬 서술": RuleMeta(
        "1인칭 화자가 자기 외모·실력을 흐뭇하게 서술하는 것이 오글거림의 최대 발생원입니다.",
        "주인공의 우월함은 타인의 대사·반응으로만 전하고, 지문은 투덜거림 쪽으로 기울이세요.",
        "거울 속의 나는 여전히 잘생겼다.", '"그 얼굴로 왜 도둑질을 하나?"\n"얼굴값이 밥값은 안 되더라."'),
    "라노벨체 감탄사": RuleMeta(
        "'오이오이·흐응·후후'는 번역 라노벨 문체 신호입니다. 즉시 이탈 요인입니다.",
        "전부 삭제하고 짧은 행동 한 줄로 대체하세요."),
    "설명충 관용구": RuleMeta(
        "'알다시피'처럼 인물이 이미 아는 정보를 독자용으로 읊는 대사는 하차 1순위입니다.",
        "정보는 흥정·불평·시비 같은 갈등에 얹어 흘리세요."),
    "악역/인물 라벨링": RuleMeta(
        "'전형적인 탐관오리였다'처럼 유형으로 규정하면 독자가 판단할 몫이 사라집니다.",
        "첫 등장은 구체적 행위 1개 + 숫자나 물건 1개로만 보여주세요."),
    "비장 선언 어휘": RuleMeta(
        "빌드업 없는 선언조 대사가 중2병·오글거림의 핵심입니다.",
        "결정적 대사는 20자 이내 평서문 하나로 끝내고, 앞 장면이 무게를 만들지 못했다면 지우세요.",
        '"이 몸 혼자서 1만 명 몫을 보여주마!"', '"나는 못 하나면 된다."'),
    "지문에 사극체(대사에만 허용)": RuleMeta(
        "지문까지 사극체면 '옷만 갈아입은 현대극'이 아니라 읽기 힘든 글이 됩니다.",
        "지문은 현대 한국어로 쓰고 사극체는 대사에만 남기세요."),
    "평균 문장 길이": RuleMeta(
        "문장이 길수록 모바일에서 체감 속도가 떨어집니다. 웹소설은 한 문장 한 사실이 기본입니다.",
        "50자 넘는 문장을 자르고 평균 20~35자를 유지하세요."),
    "50자 초과 문장 비율": RuleMeta(
        "긴 문장이 잦으면 호흡이 늘어집니다.", "긴 문장을 둘로 쪼개세요."),
    "동일 종결어미 연속": RuleMeta(
        "같은 어미가 반복되면 기계적으로 읽히고 작가 역량을 의심받습니다.",
        "'-다' 외에 '-까/-군/-지'와 명사형 종결을 섞어 호흡을 바꾸세요."),
    "다다다체(평서 종결 연속)": RuleMeta(
        "평서 종결이 길게 이어지면 단조로워집니다(다다다체).",
        "중간에 의문·명사 종결·대사를 끼워 리듬을 끊으세요."),
    "'나는'으로 시작하는 문장 비율": RuleMeta(
        "'나는 ~했다'의 반복은 초보 표식입니다.", "주어를 생략하거나 문장 순서를 바꾸세요."),
    "대사 줄 비중": RuleMeta(
        "대사가 적으면 모바일에서 벽돌처럼 읽히고 체감 속도가 급격히 떨어집니다. 히트작은 대사 비중이 큽니다.",
        "지문 덩어리를 인물 간 짧은 대화로 바꾸세요. 서술로 설명한 내용을 말다툼·흥정으로 주고받게 하면 분량과 비중이 함께 올라갑니다."),
    "연속 지문 분량(대사 없이)": RuleMeta(
        "대사 없이 이어지는 서술이 길면 '벽돌'이 되어 이탈을 부릅니다.",
        "300자를 넘기 전에 대사나 동작 한 줄로 끊으세요."),
    "벽돌 문단(120자 초과)": RuleMeta(
        "한 문단이 모바일 화면 3줄을 넘으면 읽는 부담이 커집니다.", "문단을 쪼개세요."),
    "40자 초과 장문 대사": RuleMeta(
        "긴 대사는 연극 대사처럼 들리고 티키타카가 죽습니다.",
        "40자를 넘으면 자르거나 중간에 동작 지문을 넣으세요."),
}


def rule_meta(rule: str) -> RuleMeta | None:
    return RULE_INFO.get(rule)


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
    def meta(self) -> "RuleMeta | None":
        return RULE_INFO.get(self.rule)

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
