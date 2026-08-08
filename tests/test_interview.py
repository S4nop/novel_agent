"""Behavior tests for the author interview (DESIGN §3, IdeaIntake front-end)."""
from novel_agent.interview import (
    Answer,
    InterviewPlan,
    InterviewQuestion,
    enrich_idea,
    generate_interview_questions,
    render_question,
    resolve_answer,
)


class PlanLLM:
    def __init__(self, plan):
        self.plan = plan
        self.prompt = ""

    def structured(self, messages, schema):
        self.prompt = messages[-1]["content"]
        return self.plan

    def text(self, messages, *, max_tokens=8192):  # pragma: no cover
        raise NotImplementedError


def _q(**kw):
    base = dict(
        topic="세계관 밀도", question="이 세계에 없어야 하는 것은?",
        why_it_matters="설정 과잉을 막는다",
        options=["암호화폐 없음", "해킹 없음"], default="새 기술 개념은 1개만",
    )
    return InterviewQuestion(**{**base, **kw})


def test_question_generation_asks_about_what_must_not_exist():
    llm = PlanLLM(InterviewPlan(questions=[_q()]))
    generate_interview_questions(llm, "네오 조선의 흑인 홍길동, 코믹")
    assert "없어야 하는" in llm.prompt        # the anti-concept-stacking question
    assert "설정을 과하게 쌓는 것" in llm.prompt


def test_question_generation_respects_max_questions():
    llm = PlanLLM(InterviewPlan(questions=[_q(topic=f"t{i}") for i in range(20)]))
    assert len(generate_interview_questions(llm, "아이디어", max_questions=6)) == 6


def test_numeric_input_selects_the_listed_option():
    assert resolve_answer(_q(), "2") == "해킹 없음"


def test_empty_or_whitespace_input_falls_back_to_default():
    assert resolve_answer(_q(), "") == "새 기술 개념은 1개만"
    assert resolve_answer(_q(), "   ") == "새 기술 개념은 1개만"


def test_free_text_answer_is_kept_verbatim():
    assert resolve_answer(_q(), "조선 기술 수준 그대로") == "조선 기술 수준 그대로"


def test_out_of_range_number_is_treated_as_free_text():
    assert resolve_answer(_q(), "9") == "9"


def test_rendered_question_shows_options_and_default():
    text = render_question(_q(), 1, 3)
    assert "[1/3]" in text
    assert "1) 암호화폐 없음" in text
    assert "새 기술 개념은 1개만" in text


def test_enriched_idea_carries_answers_and_forbids_inventing_beyond_them():
    idea = enrich_idea(
        "네오 조선의 흑인 홍길동, 코믹",
        [Answer(topic="세계관 밀도", question="q", answer="암호화폐·데이터 수탈 없음")],
    )
    assert "네오 조선의 흑인 홍길동, 코믹" in idea
    assert "암호화폐·데이터 수탈 없음" in idea
    assert "새로 상상하지 마세요" in idea      # the instruction downstream nodes inherit


def test_idea_is_unchanged_when_no_interview_was_run():
    assert enrich_idea("원본 아이디어", []) == "원본 아이디어"


# ── hard rules vs soft preferences must not be conflated (tester feedback 1) ──
def _hard(**kw):
    base = dict(topic="절대 금지 설정", question="이 세계에 없어야 하는 것은?",
                why_it_matters="하드 룰", answer_type="freeform", hard_rule=True,
                options=[], default="없음")
    return InterviewQuestion(**{**base, **kw})


def test_required_topics_ask_density_and_forbidden_separately():
    from novel_agent.interview import REQUIRED_TOPICS
    density = [t for t in REQUIRED_TOPICS if "밀도" in t]
    forbidden = [t for t in REQUIRED_TOPICS if "금지" in t]
    assert len(density) == 1 and len(forbidden) == 1
    assert density[0] != forbidden[0]                   # two topics, not one
    assert "없어야" not in density[0]                    # density no longer carries it


def test_prompt_instructs_one_concern_per_question():
    llm = PlanLLM(InterviewPlan(questions=[_q()]))
    generate_interview_questions(llm, "아이디어")
    assert "한 질문은 한 가지만" in llm.prompt
    assert "별개의 질문" in llm.prompt


def test_hard_rules_render_as_their_own_non_negotiable_section():
    idea = enrich_idea("전제", [
        Answer(topic="세계관 밀도", question="q", answer="설정 2~3개만", hard_rule=False),
        Answer(topic="절대 금지 설정", question="q", answer="환생 트로프, 로맨스 서브플롯",
               hard_rule=True),
    ])
    assert "절대 금지 — 협상 불가" in idea
    assert "환생 트로프" in idea.split("[작가가 정한 방향(선호)")[0]   # in the HARD block
    assert "설정 2~3개만" in idea.split("[작가가 정한 방향(선호)")[1]  # in the SOFT block


def test_soft_only_answers_produce_no_forbidden_section():
    idea = enrich_idea("전제", [Answer(topic="톤", question="q", answer="건조하게")])
    assert "절대 금지" not in idea


def test_hard_rules_helper_returns_verbatim_answers():
    from novel_agent.interview import hard_rules
    answers = [Answer(topic="금지", question="q", answer="환생", hard_rule=True),
               Answer(topic="톤", question="q", answer="건조", hard_rule=False)]
    assert hard_rules(answers) == ["환생"]


def test_freeform_question_tells_the_author_it_is_a_hard_rule():
    text = render_question(_hard(), 1, 3)
    assert "[금지 규칙]" in text
    assert "자유롭게 적으세요" in text


def test_required_topics_are_genre_agnostic():
    """Invariant #1 — no topic may name a specific work, setting, or premise."""
    from novel_agent.interview import REQUIRED_TOPICS
    leaked = ["홍길동", "조선", "헌터", "회귀", "사이버펑크", "이방인"]
    for topic in REQUIRED_TOPICS:
        assert not any(w in topic for w in leaked), f"genre leak in: {topic}"
