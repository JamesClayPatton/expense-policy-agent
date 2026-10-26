"""The two comparison implementations. rules_only hits the database only;
single_prompt makes one real model call."""
from evals.baselines import REQUIRED_TOOLS, clean_ids, rules_only, single_prompt
from expense_agent.retrieval import all_sections

LUNCH = {"employee_id": "E019", "amount": "28.50", "category": "meals",
         "transaction_date": "2026-10-21", "submitted_date": "2026-10-22",
         "vendor": "Corner Deli", "approver_id": "E011",
         "notes": "Lunch while working through the audit checklist.",
         "receipt_text": "Corner Deli. Turkey sandwich, chips, drink. Total 28.50."}
DUPLICATE = {**LUNCH, "employee_id": "E014", "amount": "58.40", "vendor": "Blue Door Bistro",
             "transaction_date": "2026-10-15", "approver_id": ""}


def test_rules_only_approves_when_no_hard_rule_breaks():
    out = rules_only({"request": LUNCH})
    assert out["decision"] == "approve" and out["policy_ids"] == ["§1.2"]
    assert out["tools_called"] == REQUIRED_TOOLS and not out["guard_overruled"]


def test_rules_only_flags_a_duplicate_and_cites_the_rule():
    out = rules_only({"request": DUPLICATE})
    assert out["decision"] == "flag" and "§2.3" in out["policy_ids"]
    assert "duplicate" in out["reason"]


def test_all_sections_is_the_whole_policy_in_order():
    ids = [s.section_id for s in all_sections()]
    assert len(ids) == 20 and ids[0] == "§1.1" and ids[-1] == "§9.2"


def test_clean_ids_repairs_the_section_sign_structured_output_mangles():
    """gpt-5-mini's JSON mode sometimes emits § as a broken escape: '\\x00a74.1'."""
    assert clean_ids(["\x00a74.1", " \x00a78.2", "§2.1", "4.3", "§2.1"]) == ["§4.1", "§8.2", "§2.1", "§4.3"]


def test_single_prompt_decides_with_the_whole_policy_and_no_tools():
    out = single_prompt({"request": LUNCH})
    assert out["decision"] in ("approve", "flag", "reject") and out["policy_ids"]
    assert len(out["retrieved_ids"]) == 20 and out["tools_called"] == []
