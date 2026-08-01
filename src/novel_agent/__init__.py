"""novel_agent — AI agent that writes serialized Korean web novels (웹소설).

Architecture and rationale live in DESIGN.md at the repo root. This package
implements Part ① (the agent workflow). The single model for every LLM role is
Gemini 3.6 Flash; all human-facing output is Korean (see config.Settings.ui_language).

This first increment is the model-independent keystone (DESIGN §3/§5):
artifacts → canon store → ledgers → ContextPackBuilder, all unit-tested.
"""
