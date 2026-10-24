"""One real run through the agent. Costs a few LLM calls and about 20 seconds."""
from decimal import Decimal

from expense_agent.agent import review_expense
from expense_agent.graph import format_request

LUNCH = {
    "employee_id": "E019", "amount": Decimal("28.50"), "category": "meals",
    "transaction_date": "2026-10-21", "submitted_date": "2026-10-22",
    "vendor": "Corner Deli", "approver_id": "E011",
    "notes": "Lunch while working through the audit checklist.",
    "receipt_text": "Corner Deli. Turkey sandwich, chips, drink. Total 28.50.",
}


def test_format_request_shows_every_field():
    text = format_request({**LUNCH, "approver_id": "", "notes": ""})
    assert "Amount: $28.50" in text
    assert "Approval on record: none" in text
    assert "Employee notes: (none)" in text


def test_in_policy_lunch_is_approved():
    r = review_expense(LUNCH, record=False)
    assert r.decision == "approve"
    assert r.model_decision == "approve" and not r.guard_overruled and r.guard_reason is None
    assert r.decision_id is None and r.run_id
    assert [t["name"] for t in r.tool_results] == r.tools_called
    assert "§4.1" in r.policy_ids
    assert set(r.tools_called) >= {"get_employee", "get_budget_remaining"}
    assert r.trace_url and r.trace_url.startswith("https://smith.langchain.com/")
