"""Tool tests against the seeded database. Run with: pytest

Every expectation comes from the situations listed at the top of db/seed.sql.
The one write (record_decision) runs inside a transaction that is rolled back.
"""
from datetime import date
from decimal import Decimal

import pytest

from expense_agent.db import connect
from expense_agent.tools import (find_duplicates, get_budget_remaining, get_employee,
                                 record_decision)


@pytest.fixture(scope="module")
def conn():
    with connect() as c:
        yield c
        c.rollback()


# get_employee -----------------------------------------------------------------

def test_employee_has_role_and_limit(conn):
    e = get_employee("E012", conn=conn)
    assert e["found"] and e["name"] == "Omar Haddad"
    assert e["role"] == "employee" and e["max_approval"] == Decimal("0")
    assert e["manager_id"] == "E007" and e["active"] is True


def test_manager_limit_is_500(conn):
    assert get_employee("E009", conn=conn)["max_approval"] == Decimal("500")


def test_inactive_employee(conn):
    assert get_employee("E020", conn=conn)["active"] is False


def test_unknown_employee(conn):
    assert get_employee("E999", conn=conn) == {"found": False, "employee_id": "E999"}


# get_budget_remaining ---------------------------------------------------------

@pytest.mark.parametrize("employee, category, remaining", [
    ("E012", "meals", "30.00"),
    ("E015", "travel", "150.00"),
    ("E017", "software", "20.00"),
    ("E013", "training", "50.00"),
    ("E018", "office_supplies", "10.00"),
    ("E009", "client_entertainment", "100.00"),
    ("E010", "team_events", "20.00"),
])
def test_planted_budgets(conn, employee, category, remaining):
    b = get_budget_remaining(employee, category, conn=conn)
    assert b["month"] == "2026-10"
    assert b["remaining"] == Decimal(remaining)


def test_rejected_claims_do_not_count(conn):
    # E016 travel in October: 140 + 140 counted, the rejected 220 hotel is not.
    b = get_budget_remaining("E016", "travel", conn=conn)
    assert b["spent"] == Decimal("280.00")


def test_last_month_does_not_count(conn):
    # E012 bought a webcam in September; October hardware is untouched.
    assert get_budget_remaining("E012", "hardware", conn=conn)["spent"] == 0


def test_month_follows_transaction_date(conn):
    b = get_budget_remaining("E012", "meals", month_of=date(2026, 9, 15), conn=conn)
    assert b["month"] == "2026-09" and b["spent"] == Decimal("55.00")


def test_other_has_no_limit(conn):
    b = get_budget_remaining("E012", "other", conn=conn)
    assert b["found"] and b["monthly_limit"] is None and b["remaining"] is None


def test_unknown_category(conn):
    assert get_budget_remaining("E012", "snacks", conn=conn)["found"] is False


# find_duplicates --------------------------------------------------------------

def test_exact_duplicate(conn):
    d = find_duplicates("E014", "Blue Door Bistro", Decimal("58.40"), date(2026, 10, 14), conn=conn)
    assert [m["kind"] for m in d["matches"]] == ["exact_duplicate"]
    assert d["matches"][0]["expense_id"] == "X007" and d["matches"][0]["status"] == "approved"


def test_near_duplicate_within_seven_days(conn):
    d = find_duplicates("E016", "SkyPark Airport Parking", Decimal("140.00"), date(2026, 10, 12), conn=conn)
    assert sorted(m["expense_id"] for m in d["matches"]) == ["X011", "X012"]
    assert {m["kind"] for m in d["matches"]} == {"near_duplicate"}


def test_same_vendor_different_amount_is_possible_split(conn):
    d = find_duplicates("E013", "Dell Direct", Decimal("350.00"), date(2026, 10, 21), conn=conn)
    assert [m["kind"] for m in d["matches"]] == ["same_vendor"]
    assert d["matches"][0]["expense_id"] == "X023"


def test_vendor_match_ignores_case_and_spaces(conn):
    d = find_duplicates("E014", "  blue door bistro ", Decimal("58.40"), date(2026, 10, 14), conn=conn)
    assert len(d["matches"]) == 1


def test_seven_days_is_still_inside_the_window(conn):
    d = find_duplicates("E014", "Blue Door Bistro", Decimal("58.40"), date(2026, 10, 21), conn=conn)
    assert [m["kind"] for m in d["matches"]] == ["near_duplicate"]


def test_rejected_claim_is_returned_with_its_status(conn):
    # E016's Hotel Lumen claim was rejected; a resubmission must see that (policy 2.3).
    d = find_duplicates("E016", "Hotel Lumen", Decimal("220.00"), date(2026, 10, 6), conn=conn)
    assert len(d["matches"]) == 1
    assert d["matches"][0]["status"] == "rejected" and d["matches"][0]["kind"] == "exact_duplicate"


def test_outside_window_is_not_a_match(conn):
    d = find_duplicates("E014", "Blue Door Bistro", Decimal("58.40"), date(2026, 10, 30), conn=conn)
    assert d["matches"] == []


def test_other_employees_are_ignored(conn):
    # E012 also ate at Blue Door Bistro for 58.40, but that is E012's claim.
    d = find_duplicates("E013", "Blue Door Bistro", Decimal("58.40"), date(2026, 10, 14), conn=conn)
    assert d["matches"] == []


# record_decision --------------------------------------------------------------

def test_record_decision_writes_audit_row():
    with connect() as c:
        out = record_decision(
            request_id="test-1", employee_id="E012", amount=Decimal("45.00"), category="meals",
            model_decision="approve", final_decision="flag", reason="over budget",
            policy_ids=["§8.1"], guard_overruled=True, guard_reason="budget exceeded",
            langsmith_run_id=None, request={"amount": Decimal("45.00"), "vendor": "Harbor Grill"},
            retrieved_ids=["§4.1", "§8.1"], tool_results=[{"name": "get_employee", "content": {"found": True}}],
            prompt_commit="3837744f", model="gpt-5-mini", conn=c,
        )
        with c.cursor() as cur:
            cur.execute("""select final_decision, guard_overruled, policy_ids, request->>'amount',
                                  retrieved_ids, tool_results->0->>'name', prompt_commit
                           from decisions where decision_id = %s""", (out["decision_id"],))
            assert cur.fetchone() == ("flag", True, ["§8.1"], "45.00", ["§4.1", "§8.1"],
                                      "get_employee", "3837744f")
            cur.execute("delete from decisions where decision_id = %s", (out["decision_id"],))
        c.commit()
