"""Guard tests against the seeded database. No LLM calls."""
from decimal import Decimal

import pytest

from expense_agent.db import connect
from expense_agent.guard import Verdict, check, guard

LUNCH = {
    "employee_id": "E019", "amount": Decimal("28.50"), "category": "meals",
    "transaction_date": "2026-10-21", "vendor": "Corner Deli", "approver_id": "E011",
}


@pytest.fixture(scope="module")
def conn():
    with connect() as c:
        yield c
        c.rollback()


# check: every hard rule, one at a time ------------------------------------------

def test_clean_approval_breaks_no_rule(conn):
    assert check(LUNCH, ["§4.1"], conn=conn) == []


def test_small_amount_needs_no_approval(conn):
    assert check({**LUNCH, "approver_id": ""}, ["§4.1"], conn=conn) == []


def test_no_citation(conn):
    assert check(LUNCH, [], conn=conn) == ["no policy section cited"]


def test_made_up_citation(conn):
    assert check(LUNCH, ["§4.1", "§12.9"], conn=conn) == ["cited section §12.9 does not exist"]


def test_unknown_employee(conn):
    assert check({**LUNCH, "employee_id": "E999"}, ["§4.1"], conn=conn) == ["employee E999 not found"]


def test_inactive_employee(conn):
    reasons = check({**LUNCH, "employee_id": "E020", "approver_id": "E008"}, ["§4.1"], conn=conn)
    assert reasons == ["employee E020 is not active (§1.1)"]


def test_over_250_without_approval(conn):
    reasons = check({**LUNCH, "amount": Decimal("250.01"), "approver_id": ""}, ["§4.1"], conn=conn)
    assert reasons == ["$250.01 is over $250 with no approval on record (§1.2)"]


def test_late_submission(conn):
    """Day 30 is on time, day 31 is late. Both after runs got this wrong in the model."""
    on_time = {**LUNCH, "transaction_date": "2026-09-22", "submitted_date": "2026-10-22"}
    late = {**LUNCH, "transaction_date": "2026-09-21", "submitted_date": "2026-10-22"}
    assert check(on_time, ["§4.1"], conn=conn) == []
    assert check(late, ["§4.1"], conn=conn) == [
        "submitted 31 days after the transaction, over the 30-day limit (§1.1)"]


def test_reviewing_the_same_expense_again_is_not_a_duplicate(conn):
    """Decisions are an audit log, not claims: running a demo example twice must
    not make the second run a duplicate of the first, or eat into the budget."""
    from expense_agent.tools import record_decision
    assert check(LUNCH, ["§4.1"], conn=conn) == []
    record_decision(request_id="demo", employee_id=LUNCH["employee_id"], amount=LUNCH["amount"],
                    category="meals", model_decision="approve", final_decision="approve",
                    reason="Fine.", policy_ids=["§4.1"], request=LUNCH, conn=conn)
    assert check(LUNCH, ["§4.1"], conn=conn) == []


def test_unknown_approver(conn):
    assert check({**LUNCH, "approver_id": "E999"}, ["§4.1"], conn=conn) == ["approver E999 not found"]


def test_approver_cannot_be_claimant(conn):
    reasons = check({**LUNCH, "employee_id": "E011", "approver_id": "E011"}, ["§4.1"], conn=conn)
    assert reasons == ["approver is the claimant (§2.1)"]


def test_approver_limit_below_amount(conn):
    hotel = {**LUNCH, "amount": Decimal("600"), "category": "travel", "vendor": "Hotel Lumen"}
    reasons = check(hotel, ["§3.2"], conn=conn)
    assert reasons == ["approver E011 (manager) may approve up to $500.00, not $600.00 (§2.1)"]


def test_plain_employee_cannot_approve(conn):
    reasons = check({**LUNCH, "approver_id": "E012"}, ["§4.1"], conn=conn)
    assert reasons == ["approver E012 (employee) may approve up to $0.00, not $28.50 (§2.1)"]


def test_duplicate_claim(conn):
    repeat = {**LUNCH, "employee_id": "E014", "amount": Decimal("58.40"),
              "transaction_date": "2026-10-15", "vendor": "Blue Door Bistro", "approver_id": ""}
    assert check(repeat, ["§4.1"], conn=conn) == ["near duplicate of X007 (approved) (§2.3)"]


def test_over_budget(conn):
    dinner = {**LUNCH, "employee_id": "E012", "amount": Decimal("45.00"),
              "vendor": "Harbor Grill", "approver_id": ""}
    assert check(dinner, ["§4.1"], conn=conn) == ["meals budget has $30.00 left this month, not $45.00 (§8.1)"]


def test_category_without_a_budget_is_not_over_budget(conn):
    assert check({**LUNCH, "category": "other"}, ["§9.2"], conn=conn) == []


def test_possible_split_is_left_to_the_model(conn):
    # Same vendor, different amount: policy 2.2 needs judgement, so the guard stays out.
    second = {**LUNCH, "employee_id": "E013", "amount": Decimal("350.00"), "category": "hardware",
              "vendor": "Dell Direct", "approver_id": "E003"}
    assert check(second, ["§2.2"], conn=conn) == []


def test_several_broken_rules_are_all_reported(conn):
    hotel = {**LUNCH, "amount": Decimal("600"), "category": "travel", "vendor": "Hotel Lumen",
             "approver_id": ""}
    assert check(hotel, [], conn=conn) == ["no policy section cited",
                       "$600.00 is over $250 with no approval on record (§1.2)"]


# guard: what happens to the decision ------------------------------------------

def test_guard_downgrades_a_bad_approve_to_flag(conn):
    v = guard("approve", LUNCH, [], conn=conn)
    assert v == Verdict("flag", overruled=True, reason="no policy section cited")


def test_guard_passes_a_clean_approve(conn):
    assert guard("approve", LUNCH, ["§4.1"], conn=conn) == Verdict("approve")


def test_guard_leaves_flag_and_reject_alone(conn):
    assert guard("flag", LUNCH, [], conn=conn) == Verdict("flag")
    assert guard("reject", LUNCH, [], conn=conn) == Verdict("reject")
