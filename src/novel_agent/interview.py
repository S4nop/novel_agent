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
from .prompt_store import load, render



class InterviewQuestion(BaseModel):
    topic: str = Field(description="이 질문이 결정하는 것 (예: 코미디 톤, 세계관 밀도)")
    question: str = Field(description="작가에게 실제로 물을 한국어 질문")
    why_it_matters: str = Field(description="이 답이 결과물의 무엇을 바꾸는지 한 줄")
    answer_type: str = Field(
        default="choice",
        description="choice = 객관식 / freeform = 자유 서술. 하드 금지 규칙은 반드시 freeform")
    hard_rule: bool = Field(
        default=False,
        description="true면 이 답은 협상 불가 금지 규칙(하드 룰). 취향/선호는 false")
    options: list[str] = Field(description="객관식일 때의 선택지 2~4개 (freeform이면 빈 목록)")
    default: str = Field(description="작가가 답을 건너뛸 경우 쓸 무난한 기본값")


class InterviewPlan(BaseModel):
    questions: list[InterviewQuestion]


class Answer(BaseModel):
    topic: str
    question: str
    answer: str
    hard_rule: bool = False   # True → 협상 불가 금지 규칙으로 하위 프롬프트에 주입


# Decisions the agent otherwise silently guesses — and got wrong at least once.
# Genre-agnostic by construction: no topic names a specific work or setting
# (invariant #1 — genre lives in GenreProfile as data, never hardcoded).
# Genre-agnostic BY CONSTRUCTION (invariant #1): not one topic may name a genre,
# a trope, or a work. An earlier version listed "코미디 톤", "원작·기존 IP" and
# "주인공의 가장 두드러진 설정" — all three were artifacts of one test idea
# ("네오 조선의 흑인 홍길동, 코믹"), and they forced every interview toward
# comedy and adaptation. A 무협 복수극 was asked where its comedy sat.
REQUIRED_TOPICS = [
    "작품의 톤 — 이 아이디어를 어떤 태도로 다룰 것인가 (아이디어에 맞는 선택지로)",
    "세계관 밀도 — 새 설정을 몇 개까지 허용하는가 (취향, 객관식)",
    "절대 금지 설정 — 이 세계에 '없어야 하는' 것 (하드 룰, 자유 서술)",
    "이 세계의 상식 수준 — 무엇이 당연하고 무엇이 놀라운 일인가",
    "주인공이 처한 출발 지점 — 무엇을 원하고 무엇이 막고 있는가",
    "갈등의 규모 (개인적 다툼 / 조직·집단 / 세계 단위)",
    "독자 대상과 등급",
]


def generate_interview_questions(
    llm: LLM, idea: str, *, max_questions: int = 10
) -> list[InterviewQuestion]:
    """Ask the model to propose the highest-leverage questions for THIS idea."""
    plan = llm.structured(
        [
            {"role": "system", "content": load("interview_system")},
            {"role": "user", "content": render(
                "interview_request", idea=idea, max_questions=max_questions,
                required_topics=chr(10).join(f"- {t}" for t in REQUIRED_TOPICS))},
        ],
        InterviewPlan,
    )
    return plan.questions[:max_questions]


def render_question(q: InterviewQuestion, index: int, total: int) -> str:
    """Human-facing Korean prompt for one question."""
    tag = "  ※ 하드 룰 — 여기 적은 것은 절대 등장하지 않습니다" if q.hard_rule else ""
    lines = [f"\n[{index}/{total}] {q.topic}{'  [금지 규칙]' if q.hard_rule else ''}",
             f"  {q.question}"]
    for i, opt in enumerate(q.options, 1):
        lines.append(f"    {i}) {opt}")
    if q.answer_type == "freeform" or not q.options:
        lines.append("  (자유롭게 적으세요. 쉼표로 여러 개 가능)")
    lines.append(f"  (엔터 = 기본값: {q.default})")
    if tag:
        lines.append(tag)
    return "\n".join(lines)


# An author with no opinion must be able to say so. Silently substituting the
# model's default and then labelling it "작가가 정한 방향" manufactures authorial
# intent: a 무협 복수극 run answered nothing and still carried
# "코미디 톤: 건조한 풍자" into every downstream prompt.
SKIPPED = "\x00skip"
_SKIP_TOKENS = {"-", "skip", "s", "건너뛰기", "패스", "없음", "무관"}


def resolve_answer(q: InterviewQuestion, raw: str) -> str:
    """Option pick, free text, explicit default, or SKIPPED.

    Empty input means SKIP, not "use the default" — not answering is the
    author's most common state and it must not become a recorded preference.
    To take the suggested value they type it or pick its number.
    """
    raw = (raw or "").strip()
    if not raw or raw.lower() in _SKIP_TOKENS:
        return SKIPPED
    if raw.isdigit() and 1 <= int(raw) <= len(q.options):
        return q.options[int(raw) - 1]
    return raw


def was_answered(a: "Answer") -> bool:
    return a.answer != SKIPPED and bool(a.answer.strip())


def _answered(answers: list[Answer]) -> list[Answer]:
    return [a for a in answers if was_answered(a)]


def hard_rules(answers: list[Answer]) -> list[str]:
    """Only the non-negotiable answers the author actually gave, verbatim."""
    return [a.answer.strip() for a in _answered(answers) if a.hard_rule]


def preferences(answers: list[Answer]) -> list[Answer]:
    """Skipped questions are not preferences — omitting them is the point."""
    return [a for a in _answered(answers) if not a.hard_rule]


def enrich_idea(idea: str, answers: list[Answer]) -> str:
    """The `Idea` artifact = premise + interview answers (DESIGN §1).

    Hard rules and soft preferences are rendered as SEPARATE sections. Previously
    both were concatenated into one list, so a downstream model had to guess which
    answers were negotiable — and the density question's option text carried a
    forbidden-setting clause inside it.
    """
    if not _answered(answers):
        return idea        # nothing the author actually chose
    parts = [idea]
    hard = hard_rules(answers)
    if hard:
        parts.append(
            "[절대 금지 — 협상 불가. 아래 항목은 어떤 형태로도 등장시키지 마세요]\n"
            + "\n".join(f"- {h}" for h in hard)
        )
    soft = preferences(answers)
    if soft:
        parts.append(
            "[작가가 정한 방향(선호) — 이 범위를 따르고, 여기서 벗어난 설정을 "
            "새로 상상하지 마세요]\n"
            + "\n".join(f"- {a.topic}: {a.answer}" for a in soft)
        )
    return "\n\n".join(parts)
