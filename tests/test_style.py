"""Behavior tests for the prose style lint (DESIGN §5 Track C).

Thresholds come from Korean 웹소설 practitioner craft sources — see docs/style-spec.md.
"""
from novel_agent.style import lint_prose, style_score


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
