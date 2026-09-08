import pytest
from llampaca.agent.prompts import build_system_prompt

def test_system_prompt_budget_enforced():
    huge_wiki = "word " * 5000  # way over budget
    prompt = build_system_prompt(
        base_prompt="Be helpful.",
        wiki_index=huge_wiki,
        skills_index="",
        tool_definitions=[],
        context_size=4096,
    )
    # 40% of 4096 = 1638 tokens * 4 chars = ~6550 chars
    assert len(prompt) < 7000
