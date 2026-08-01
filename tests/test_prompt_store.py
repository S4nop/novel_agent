"""Behavior tests for the editable prompt store.

The point of these: a non-engineer will edit `prompts/*.md`. A broken edit must
FAIL LOUDLY, never silently starve the model of data — a prompt that quietly
lost its `${idea}` would degrade every downstream artifact with no error.
"""
import pytest

from novel_agent import prompt_store as ps


@pytest.fixture(autouse=True)
def _fresh_cache():
    ps.clear_cache()
    yield
    ps.clear_cache()


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVEL_PROMPTS_DIR", str(tmp_path))
    ps.clear_cache()
    return tmp_path


# ── the shipped prompt set must be valid ─────────────────────────────────────
def test_every_shipped_prompt_is_present_and_valid():
    names = ps.list_prompts()
    assert "drafter_system" in names and "style_rules" in names
    for name in names:
        assert ps.validate(name) == [], f"{name}.md has problems: {ps.validate(name)}"


def test_readme_is_not_exposed_as_an_editable_prompt():
    assert "_README" not in ps.list_prompts()


# ── rendering ────────────────────────────────────────────────────────────────
def test_render_substitutes_values(sandbox):
    (sandbox / "genre_inference.md").write_text("아이디어: ${idea}", encoding="utf-8")
    assert ps.render("genre_inference", idea="네오 조선") == "아이디어: 네오 조선"


def test_prose_containing_braces_is_left_alone(sandbox):
    """Korean prompts contain [brackets] and sometimes {json}; only ${x} is special."""
    (sandbox / "genre_inference.md").write_text(
        '{"beat": 1} [아이디어] ${idea}', encoding="utf-8")
    out = ps.render("genre_inference", idea="X")
    assert '{"beat": 1}' in out and "[아이디어]" in out


# ── guardrails against a bad edit ────────────────────────────────────────────
def test_deleting_a_required_placeholder_is_rejected(sandbox):
    (sandbox / "genre_inference.md").write_text("장르를 추론하세요", encoding="utf-8")
    problems = ps.validate("genre_inference")
    assert problems and "idea" in problems[0]
    with pytest.raises(ValueError, match="idea"):
        ps.render("genre_inference", idea="X")


def test_a_typo_in_a_placeholder_is_reported(sandbox):
    (sandbox / "genre_inference.md").write_text("${idea} ${ideaa}", encoding="utf-8")
    problems = ps.validate("genre_inference")
    assert any("ideaa" in p for p in problems)


def test_empty_prompt_is_reported(sandbox):
    (sandbox / "analyst_system.md").write_text("   ", encoding="utf-8")
    assert "비어 있습니다" in " ".join(ps.validate("analyst_system"))


def test_save_refuses_an_invalid_edit_and_keeps_the_old_text(sandbox):
    good = "아이디어: ${idea}"
    (sandbox / "genre_inference.md").write_text(good, encoding="utf-8")
    with pytest.raises(ValueError):
        ps.save("genre_inference", "자리표시자를 지운 버전")
    assert (sandbox / "genre_inference.md").read_text(encoding="utf-8") == good


def test_save_accepts_a_valid_edit_and_takes_effect_immediately(sandbox):
    (sandbox / "genre_inference.md").write_text("원본 ${idea}", encoding="utf-8")
    ps.render("genre_inference", idea="X")            # populate the cache
    ps.save("genre_inference", "수정본 ${idea}")
    assert ps.render("genre_inference", idea="X") == "수정본 X"   # cache invalidated


def test_missing_file_names_the_available_prompts(sandbox):
    (sandbox / "analyst_system.md").write_text("x", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="analyst_system"):
        ps.load("nope")


def test_prompts_dir_can_be_overridden_for_ab_testing(sandbox):
    (sandbox / "analyst_system.md").write_text("실험용 프롬프트", encoding="utf-8")
    assert ps.load("analyst_system") == "실험용 프롬프트"


# ── the wiring actually reads the files ──────────────────────────────────────
def test_editing_style_rules_changes_the_drafting_prompt(sandbox):
    from novel_agent.prompts import drafting_system_prompt

    from .factories import genre_profile

    (sandbox / "style_rules.md").write_text("규칙: 짧게 쓴다", encoding="utf-8")
    (sandbox / "drafter_system.md").write_text(
        "${audience}/${content_rating}/${sub_genre}/${pov}/${tense}/${length_target}\n"
        "${style_rules}", encoding="utf-8")
    out = drafting_system_prompt(genre_profile())
    assert "규칙: 짧게 쓴다" in out
    assert "남성향" in out
