"""The prompts in LangSmith must expose exactly the variables the code fills in."""
import pytest

from expense_agent.prompts import AGENT_PROMPT, JUDGE_PROMPT, Prompt, load_prompt


def test_agent_prompt_variables():
    p = load_prompt(AGENT_PROMPT)
    assert len(p.commit) >= 8
    assert p.variables == {"today", "policy_sections", "request"}


def test_judge_prompt_variables():
    p = load_prompt(JUDGE_PROMPT)
    assert p.variables == {"request", "policy_sections", "tool_results",
                           "decision", "reason", "reference"}


def test_messages_fill_every_variable():
    p = Prompt("t", "abc", "Today is {today}.", "{request}")
    assert p.messages(today="2026-10-22", request="lunch") == [
        ("system", "Today is 2026-10-22."), ("human", "lunch")]


def test_missing_variable_is_an_error():
    p = Prompt("t", "abc", "Today is {today}.", "{request}")
    with pytest.raises(KeyError, match="request"):
        p.messages(today="2026-10-22")
