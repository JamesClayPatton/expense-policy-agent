"""The deterministic evaluators, fed by hand. No LLM, no database."""
import pytest

from evals.checks import (citation, citations_exist, decision, error_cost, guardrail,
                          retrieval, tools)

EXPECTED = {"expected": {"decision": "approve", "policy_ids": ["§4.1"]}}


def out(**kw):
    base = {"decision": "approve", "reason": "Fine.", "policy_ids": ["§4.1"],
            "retrieved_ids": ["§4.1", "§8.1"],
            "tools_called": ["get_employee", "get_budget_remaining", "find_duplicates"]}
    return {**base, **kw}


def test_decision_matches():
    assert decision(out(), EXPECTED) == {"key": "decision", "score": 1}
    assert decision(out(decision="flag"), EXPECTED) == {"key": "decision", "score": 0}


@pytest.mark.parametrize("expected, got, cost", [
    ("approve", "approve", 0.0),
    ("flag", "approve", 1.0),      # the expensive mistake: money goes out
    ("reject", "approve", 1.0),
    ("approve", "flag", 0.25),     # a person spends a few minutes
    ("approve", "reject", 0.5),    # a person spends longer, and is annoyed
    ("flag", "reject", 0.25),
    ("reject", "flag", 0.25),
])
def test_error_cost_weights_wrong_approvals_hardest(expected, got, cost):
    ref = {"expected": {"decision": expected, "policy_ids": []}}
    assert error_cost(out(decision=got), ref) == {"key": "error_cost", "score": cost}


def test_citation_needs_one_of_the_expected_sections():
    ref = {"expected": {"decision": "flag", "policy_ids": ["§1.2", "§2.1"]}}
    assert citation(out(policy_ids=["§2.1", "§3.1"]), ref)["score"] == 1
    assert citation(out(policy_ids=["§3.1"]), ref)["score"] == 0
    assert citation(out(policy_ids=[]), ref)["score"] == 0


def test_citations_exist_rejects_made_up_sections():
    known = {"§4.1", "§8.1"}
    assert citations_exist(out(), known)["score"] == 1
    assert citations_exist(out(policy_ids=["§4.1", "§12.9"]), known)["score"] == 0
    assert citations_exist(out(policy_ids=[]), known)["score"] == 0


def test_tools_requires_all_three_lookups():
    assert tools(out(), {"request": {"employee_id": "E012"}})["score"] == 1
    assert tools(out(tools_called=["get_employee"]), {"request": {"employee_id": "E012"}})["score"] == 0


def test_tools_only_needs_the_employee_lookup_when_the_employee_is_unknown():
    assert tools(out(tools_called=["get_employee"]), {"request": {"employee_id": "E999"}})["score"] == 1


def test_guardrail_only_scores_cases_that_have_forbidden_text():
    assert guardrail(out(), EXPECTED) == {"results": []}
    ref = {"expected": {"decision": "approve", "policy_ids": [], "must_not_say": ["4111", "123-45-6789"]}}
    assert guardrail(out(reason="Approved, breakfast is fine."), ref) == {"key": "guardrail", "score": 1}
    assert guardrail(out(reason="Card 4111 1111 noted."), ref) == {"key": "guardrail", "score": 0}


def test_retrieval_looks_at_what_was_retrieved_not_what_was_cited():
    ref = {"expected": {"decision": "flag", "policy_ids": ["§2.3"]}}
    assert retrieval(out(retrieved_ids=["§4.1", "§2.3"], policy_ids=[]), ref)["score"] == 1
    assert retrieval(out(retrieved_ids=["§4.1"], policy_ids=["§2.3"]), ref)["score"] == 0
