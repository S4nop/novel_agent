"""Author interview — the front of IdeaIntake (DESIGN §3).

Why this exists: given only "네오 조선의 흑인 홍길동, 코믹", the agent invented an
elaborate world (생체 데이터 수탈, 상평통보 코인, 넙적패드…) that the author never
asked for. Concept-stacking is what made the prose read childish. The agent was
guessing at decisions that are the AUTHOR's to make.

So before any worldbuilding, the agent interviews the author: it proposes the
questions that most change the outcome, offers quick options for each, and folds
the answers into the `Idea` artifact ("freeform premise + answers to clarifying
questions"). Every downstream artifact is then built from what the author
actually wants rather than from the model's imagination.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from .llm import LLM

_INTERVIEWER = (
    "당신은 한국 웹소설 기획 편집자입니다. 작가의 짧은 아이디어를 듣고, "
    "세계관을 대신 상상해 버리지 않기 위해 '작가에게 반드시 물어야 할 것'을 "
    "질문으로 뽑아냅니다. 출력은 모두 한국어입니다."
)


class InterviewQuestion(BaseModel):
    topic: str = Field(description="이 질문이 결정하는 것 (예: 코미디 톤, 세계관 밀도)")
    question: str = Field(description="작가에게 실제로 물을 한국어 질문")
    why_it_matters: str = Field(description="이 답이 결과물의 무엇을 바꾸는지 한 줄")
    options: list[str] = Field(description="작가가 빠르게 고를 수 있는 선택지 2~4개")
    default: str = Field(description="작가가 답을 건너뛸 경우 쓸 무난한 기본값")


class InterviewPlan(BaseModel):
    questions: list[InterviewQuestion]


class Answer(BaseModel):
    topic: str
    question: str
    answer: str


# Decisions the agent otherwise silently guesses — and got wrong at least once.
REQUIRED_TOPICS = [
    "코미디 톤 (건조한 풍자 / 소동극 / 시트콤 중 어디쯤인가)",
    "세계관 밀도 — 새 설정을 몇 개까지 허용하는가, 이 세계에 '없어야 하는' 것은 무엇인가",
    "기술 수준 — 어디까지가 이 작품의 상식인가",
    "원작 홍길동전 요소를 얼마나 살릴 것인가",
    "주인공의 외지인·이방인 설정을 어떻게 다룰 것인가 (풍자 소재 / 배경 / 플롯 핵심)",
    "갈등의 규모 (저잣거리 소동 / 관아 / 국가 전복)",
    "독자 대상과 등급",
    "절대 넣지 말아야 할 것 (취향상 금지)",
]


def generate_interview_questions(
    llm: LLM, idea: str, *, max_questions: int = 10
) -> list[InterviewQuestion]:
    """Ask the model to propose the highest-leverage questions for THIS idea."""
    plan = llm.structured(
        [
            {"role": "system", "content": _INTERVIEWER},
            {
                "role": "user",
                "content": (
                    f"[작가의 아이디어]\n{idea}\n\n"
                    "이 아이디어만으로는 기획자가 마음대로 상상하게 되는 지점이 많습니다. "
                    f"작가에게 물어야 할 질문을 최대 {max_questions}개 뽑으세요.\n\n"
                    "반드시 아래 주제를 포함해 질문을 만드세요:\n"
                    + "\n".join(f"- {t}" for t in REQUIRED_TOPICS)
                    + "\n\n규칙:\n"
                    "- 질문은 구체적이어야 합니다. '톤은 어떻게 할까요?' 같은 막연한 질문 금지.\n"
                    "- 각 질문에는 작가가 바로 고를 수 있는 선택지를 2~4개 제시하세요.\n"
                    "- 특히 '이 세계에 없어야 하는 것'을 반드시 물어보세요. "
                    "설정을 과하게 쌓는 것이 이 작품의 최대 위험입니다."
                ),
            },
        ],
        InterviewPlan,
    )
    return plan.questions[:max_questions]


def render_question(q: InterviewQuestion, index: int, total: int) -> str:
    """Human-facing Korean prompt for one question."""
    lines = [f"\n[{index}/{total}] {q.topic}", f"  {q.question}"]
    for i, opt in enumerate(q.options, 1):
        lines.append(f"    {i}) {opt}")
    lines.append(f"  (엔터 = 기본값: {q.default})")
    return "\n".join(lines)


def resolve_answer(q: InterviewQuestion, raw: str) -> str:
    """Accept a number (option pick), free text, or empty (use the default)."""
    raw = (raw or "").strip()
    if not raw:
        return q.default
    if raw.isdigit() and 1 <= int(raw) <= len(q.options):
        return q.options[int(raw) - 1]
    return raw


def enrich_idea(idea: str, answers: list[Answer]) -> str:
    """The `Idea` artifact = freeform premise + interview answers (DESIGN §1)."""
    if not answers:
        return idea
    block = "\n".join(f"- {a.topic}: {a.answer}" for a in answers)
    return (
        f"{idea}\n\n[작가가 직접 정한 방향 — 반드시 이 답을 따르고, "
        f"여기서 벗어난 설정을 새로 상상하지 마세요]\n{block}"
    )
