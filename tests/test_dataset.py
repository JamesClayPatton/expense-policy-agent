"""Checks on the eval dataset itself: well formed, consistent with the seed data,
and never asking for an approval the guard would block."""
from collections import Counter
from datetime import date
from decimal import Decimal

import pytest

from evals.dataset import SLICES, load_cases
from expense_agent.db import connect
from expense_agent.guard import check

CASES = load_cases()
DECISIONS = {"approve", "flag", "reject"}


@pytest.fixture(scope="module")
def conn():
    with connect() as c:
        yield c
        c.rollback()


def test_enough_cases_and_every_slice_is_covered():
    counts = Counter(c["slice"] for c in CASES)
    assert len(CASES) >= 50
    assert set(counts) == set(SLICES)
    assert min(counts.values()) >= 3


def test_ids_are_unique_and_say_their_slice():
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids))
    assert all(c["id"].startswith(c["slice"] + "-") for c in CASES)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_case_is_well_formed(case):
    r, e = case["request"], case["expected"]
    assert Decimal(r["amount"]) > 0
    date.fromisoformat(r["transaction_date"])
    assert e["decision"] in DECISIONS
    assert e["policy_ids"] and all(p.startswith("§") for p in e["policy_ids"])
    assert case["reference"].strip()


def test_cited_sections_exist(conn):
    cited = {p for c in CASES for p in c["expected"]["policy_ids"]}
    with conn.cursor() as cur:
        cur.execute("select section_id from policy_chunks")
        known = {row[0] for row in cur.fetchall()}
    assert cited <= known, cited - known


def test_people_exist_unless_the_case_is_about_that(conn):
    with conn.cursor() as cur:
        cur.execute("select employee_id from employees")
        known = {row[0] for row in cur.fetchall()}
    for c in CASES:
        if "unknown" in c["id"]:
            continue
        assert c["request"]["employee_id"] in known, c["id"]
        assert (c["request"].get("approver_id") or c["request"]["employee_id"]) in known, c["id"]


def test_guard_allows_every_expected_approval(conn):
    blocked = {c["id"]: check(c["request"], c["expected"]["policy_ids"], conn=conn)
               for c in CASES if c["expected"]["decision"] == "approve"}
    assert {k: v for k, v in blocked.items() if v} == {}
