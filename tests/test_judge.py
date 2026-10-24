"""The judge: one real call to Claude on a fixed decision, plus parsing."""
from decimal import Decimal

from types import SimpleNamespace

from expense_agent.judge import SCORES, _parse, _text, judge

LUNCH = {
    "employee_id": "E019", "amount": Decimal("28.50"), "category": "meals",
    "transaction_date": "2026-10-21", "submitted_date": "2026-10-22",
    "vendor": "Corner Deli", "approver_id": "E011",
    "notes": "Lunch while working through the audit checklist.",
    "receipt_text": "Corner Deli. Turkey sandwich, chips, drink. Total 28.50.",
}
TOOL_RESULTS = [
    {"name": "get_employee", "content": {"found": True, "employee_id": "E019", "role": "employee",
                                         "active": True, "manager_id": "E011"}},
    {"name": "get_budget_remaining", "content": {"category": "meals", "monthly_limit": "300.00",
                                                 "spent": "34.10", "remaining": "265.90"}},
    {"name": "find_duplicates", "content": {"matches": []}},
]


def test_parse_strips_a_code_fence():
    assert _parse('```json\n{"grounded": 4}\n```') == {"grounded": 4}


def test_text_skips_a_thinking_block():
    blocks = [SimpleNamespace(type="thinking", thinking="hmm"),
              SimpleNamespace(type="text", text='{"grounded": 4}')]
    assert _text(blocks) == '{"grounded": 4}'


def test_judge_scores_a_grounded_reason():
    j = judge(LUNCH, ["§4.1", "§8.1"], TOOL_RESULTS, "approve",
              "The $28.50 lunch is under the $35 limit in §4.1, the employee has $265.90 "
              "of the monthly meals budget left (§8.1), manager approval is on record, "
              "and no duplicate was found.")
    assert all(1 <= getattr(j, k) <= 5 for k in SCORES)
    assert j.grounded >= 4 and j.safe >= 4
    assert set(j.evidence) == set(SCORES)
    assert j.judge_model and j.judge_prompt.startswith("expense-judge:")
