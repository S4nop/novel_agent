"""Behavior tests for the prose style lint (DESIGN §5 Track C).

Thresholds come from Korean 웹소설 practitioner craft sources — see docs/style-spec.md.
"""
from novel_agent.style import (
    forbidden_terms_from,
    lint_prose,
    rule_meta,
    style_score,
)


def _rules(text, **kw):
    return {v.rule for v in lint_prose(text, **kw)}


def test_clean_prose_passes_with_high_score():
    text = "\n".join(
        [
            '"가진 놈 것만 훔친다."',
            "놈이 칼자루를 쥐었다.",
            '"규칙인가?"',
            '"취향이다."',
            "골목이 조용해졌다. 뒷걸음질할 자리를 셌다.",
            '"세 걸음."',
        ]
    )
    assert _rules(text) == set()
    assert style_score(text) == 100


def test_flags_doubled_punctuation_as_blocker():
    v = lint_prose('"뭐야?! 저놈이 훔쳤다고?!"')
    assert any(x.rule == "겹부호(?!, !!)" and x.severity == "blocker" for x in v)


def test_flags_self_praise_narration_as_blocker():
    v = lint_prose("195센티미터의 우람한 칠흑빛 신체. 역시 나였다.")
    blockers = {x.rule for x in v if x.severity == "blocker"}
    assert "자기 칭찬 서술" in blockers


def test_flags_light_novel_interjections_as_blocker():
    v = lint_prose('"오이오이, 이건 좀 곤란한데."')
    assert any(x.rule == "라노벨체 감탄사" and x.severity == "blocker" for x in v)


def test_flags_named_emotions_and_intensifiers_in_narration():
    rules = _rules("나는 정말 분노했다. 너무 두려웠다.")
    assert "감정 직설" in rules
    assert "강도부사(지문)" in rules


def test_intensifier_in_dialogue_is_natural_speech_not_a_defect():
    """'아주 조선시대네' is good writing — the rule targets narration padding."""
    assert "강도부사(지문)" not in _rules('"시스테마가 아주 조선시대네."')


def test_intensifier_substring_inside_a_verb_is_not_a_hit():
    """'되찾아주셨다' contains '아주' across a morpheme boundary."""
    assert "강도부사(지문)" not in _rules("도사님이 데이터를 되찾아주셨다.")


def test_flags_character_labeling():
    assert "악역/인물 라벨링" in _rules("전형적인 탐관오리 양반가였다.")


def test_flags_exclamation_in_narration():
    assert "지문 내 느낌표" in _rules("문이 열렸다!")


def test_dialogue_exclamation_allowed_under_budget():
    """A few in dialogue is fine; the rule targets narration."""
    assert "지문 내 느낿표" not in _rules('"멈춰라!"')
    assert "지문 내 느낌표" not in _rules('"멈춰라!"')


def test_flags_translationese_endings():
    assert "번역체 종결" in _rules("그것은 함정인 것이다. 나는 밀려나게 되었다.")


def test_flags_sageuk_style_in_narration_but_not_dialogue():
    assert "지문에 사극체(대사에만 허용)" in _rules("나는 담을 넘었노라.")
    assert "지문에 사극체(대사에만 허용)" not in _rules('"내 이미 알고 있었노라."')


def test_flags_grand_declaration_vocabulary():
    assert "비장 선언 어휘" in _rules('"이 몸 혼자서 1만 명 몫을 보여주마."')


def test_flags_repeated_identical_sentence_endings():
    """Identical ending FORM three times running (확인했다/점검했다/계산했다)."""
    text = "장부를 확인했다. 창고를 점검했다. 손해를 계산했다."
    assert "동일 종결어미 연속" in _rules(text)


def test_varied_verbs_are_not_flagged_as_identical_endings():
    text = "담을 넘었다. 골목을 달렸다. 숨을 골랐다."
    assert "동일 종결어미 연속" not in _rules(text)


def test_flags_monotone_declarative_run():
    """Substantial sentences only — short action beats are prescribed craft."""
    text = " ".join(
        f"사내가 천천히 {w} 그리고 나를 오래 바라보았다." for w in
        ["웃었다.","일어섰다.","걸었다.","멈췄다.","돌아봤다.","앉았다."]
    )
    assert "다다다체(평서 종결 연속)" in _rules(text)


def test_short_action_staccato_is_not_flagged_as_monotone():
    text = "\n".join(["주머니를 뒤적였다.","칩을 씹었다.","불꽃이 튀었다.",
                      "포트에 꽂았다.","손끝이 저렸다.","숨을 골랐다."])
    assert "다다다체(평서 종결 연속)" not in _rules(text)


def test_flags_narration_wall_without_dialogue():
    """Ratio rules need a realistic sample (an episode has 100+ lines)."""
    text = "\n".join(f"서술 {i}번째 줄이며 문장을 충분히 길게 늘려 벽돌 문단을 만든다." for i in range(12))
    rules = _rules(text)
    assert "연속 지문 분량(대사 없이)" in rules
    assert "대사 줄 비중" in rules


def test_score_penalises_blockers_more_than_minors():
    blocker = style_score('"오이오이."')          # light-novel interjection
    minor = style_score("문단.\n" + "가" * 130)   # brick paragraph
    assert blocker < minor


def test_score_distinguishes_severity_of_the_same_violation():
    """27 exclamations and 12 are both 'one major' — but the score must show
    progress, or the reviser's keep-best can never accept a partial fix."""
    base = "\n".join(['"대사입니다."', "지문이다."] * 20)
    many = base + "\n" + "\n".join(f'"놀랐다{"!"*1}"' for _ in range(40))
    few = base + "\n" + "\n".join(f'"놀랐다{"!"*1}"' for _ in range(12))
    assert style_score(few) > style_score(many)


def test_every_lint_rule_has_a_rationale_and_a_fix():
    """A rule with no explanation reads as an arbitrary deduction (feedback 3).
    This fails loudly if someone adds a rule without documenting it."""
    from novel_agent.style import RULE_INFO
    import novel_agent.style as st

    # every rule the linter can emit must be documented
    samples = [
        '"오이오이!!" 역시 나였다. 나는 정말 분노했다. 전형적인 악당이었다!',
        "\n".join(["문장이 길어지고 또 길어지며 계속 이어지는 서술이다" * 2] * 12),
        "장부를 확인했다. 창고를 점검했다. 손해를 계산했다.",
    ]
    seen = set()
    for text in samples:
        for v in st.lint_prose(text):
            seen.add(v.rule)
            assert v.meta is not None, f"'{v.rule}' has no RuleMeta"
            assert v.meta.why and v.meta.fix
    assert len(seen) >= 6
    assert all(m.why and m.fix for m in RULE_INFO.values())


# ── the author's absolute prohibitions (테스트 피드백 1-④) ────────────────────
class TestForbiddenTerms:
    """The gate was blind to a violated hard rule: prose stuffed with the exact
    terms the author banned scored a clean 100."""

    def test_prose_using_a_banned_term_is_blocked(self):
        terms = forbidden_terms_from(hard_rules=["암호화폐, 생체 데이터 같은 현대 IT 개념"])
        prose = "케이타는 생체 데이터 기록을 훑었다.\n" * 8
        v = next(x for x in lint_prose(prose, forbidden_terms=terms)
                 if x.rule == "작가 금기어")
        assert v.severity == "blocker"
        assert "생체 데이터" in v.evidence

    def test_the_same_prose_scores_a_clean_100_without_the_terms(self):
        """The reported failure exactly: prose that violates the author's world
        was indistinguishable from perfect prose. Structurally clean on purpose,
        so it is this rule catching it and not a rhythm rule."""
        prose = ('"생체 데이터 기록입니다."\n\n케이타가 장부를 넘겼다.\n\n'
                 '"확인했나?"\n\n할멈은 고개를 저었지.\n\n엽전 두 냥이 굴렀다.\n\n') * 5
        assert style_score(prose) == 100
        assert style_score(prose, forbidden_terms=["생체 데이터"]) < 100

    def test_exemplars_are_kept_and_the_category_phrase_is_discarded(self):
        """Korean lists exemplars before the category: "X, Y 같은 Z" bans X and Y,
        not Z. Keeping Z would false-positive on ordinary words."""
        terms = forbidden_terms_from(hard_rules=["암호화폐, 생체 데이터 같은 현대 IT 개념"])
        assert set(terms) == {"암호화폐", "생체 데이터"}

    def test_a_multi_word_ban_is_not_split_into_common_nouns(self):
        """Splitting "생체 데이터" into "데이터" would flag innocent modern prose."""
        terms = forbidden_terms_from(hard_rules=["생체 데이터"])
        innocent = "그는 데이터를 확인했다. 현대적인 건물이었다.\n" * 8
        assert "데이터" not in terms
        assert not [v for v in lint_prose(innocent, forbidden_terms=terms)
                    if v.rule == "작가 금기어"]

    def test_genre_anti_patterns_are_enforced_too(self):
        terms = forbidden_terms_from(anti_patterns=["회귀", "환생"])
        prose = "그는 회귀했다.\n" * 8
        assert any(v.rule == "작가 금기어" for v in lint_prose(prose, forbidden_terms=terms))

    def test_many_banned_terms_report_as_one_finding_not_a_flood(self):
        """N breaches is one kind of failure; N blockers would swamp the score and
        bury the findings the reviser needs."""
        terms = forbidden_terms_from(hard_rules=["암호화폐, 회귀, 환생, 시스템창"])
        prose = "암호화폐와 회귀와 환생과 시스템창.\n" * 8
        hits = [v for v in lint_prose(prose, forbidden_terms=terms) if v.rule == "작가 금기어"]
        assert len(hits) == 1

    def test_the_rule_explains_itself_like_every_other_rule(self):
        """테스트 피드백 3: a bare deduction teaches the author nothing."""
        meta = rule_meta("작가 금기어")
        assert meta and meta.why and meta.fix and meta.bad and meta.good

    def test_no_terms_configured_changes_nothing(self):
        prose = "평범한 문장이다.\n" * 8
        assert lint_prose(prose) == lint_prose(prose, forbidden_terms=[])
