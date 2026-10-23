"""Retrieval tests against the ingested policy. Each search costs one embedding call.

The xfail cases are known baseline misses, kept here on purpose so the eval
run can show them before any retrieval change is made.
"""
import pytest

from expense_agent.db import connect
from expense_agent.retrieval import format_sections, get_sections, query_text, search


@pytest.fixture(scope="module")
def conn():
    with connect() as c:
        yield c


def ids(sections):
    return [s.section_id for s in sections]


def test_query_text_skips_empty_fields():
    text = query_text({"category": "meals", "vendor": "Corner Deli", "amount": "28.50",
                       "notes": "", "receipt_text": ""})
    assert "Notes" not in text and "Receipt" not in text
    assert text.startswith("Expense category: meals")


def test_client_dinner_finds_entertainment_rule(conn):
    hits = search("Dinner with a prospective client at a steakhouse, four attendees, "
                  "two from the client company", conn=conn)
    assert "§4.2" in ids(hits)[:3]
    assert all(h.score is not None for h in hits)


@pytest.mark.xfail(reason="Known baseline miss: a split purchase looks like an ordinary hardware "
                          "claim in the text, so §2.2 is not in the top 6. The split only shows "
                          "up in the find_duplicates result.", strict=False)
def test_split_purchase_finds_aggregation_rule(conn):
    hits = search("Dell Direct 27-inch monitor 400.00. Monitor for home office. hardware",
                  conn=conn)
    assert "§2.2" in ids(hits)


def test_duplicate_claim_finds_duplicate_rule(conn):
    hits = search("Same dinner receipt at Blue Door Bistro already approved last week",
                  conn=conn)
    assert "§2.3" in ids(hits)[:3]


@pytest.mark.xfail(reason="Known baseline miss: §9.2 (unaddressed cases) ranks below §8.2",
                   strict=False)
def test_crypto_finds_exceptions_rule(conn):
    hits = search("Crypto purchase at Coin Corner for a client pilot", conn=conn)
    assert "§9.2" in ids(hits)[:2]


@pytest.mark.xfail(reason="Known baseline miss: §1.1 (30-day submission rule) ranks third",
                   strict=False)
def test_late_receipt_finds_submission_rule(conn):
    hits = search("Receipt from 45 days ago, forgot to submit it", conn=conn)
    assert "§1.1" in ids(hits)[:2]


def test_get_sections_by_id(conn):
    secs = get_sections(["§8.1", "§2.1"], conn=conn)
    assert ids(secs) == ["§2.1", "§8.1"]
    assert "Monthly" in secs[1].title


def test_get_sections_ignores_unknown_ids(conn):
    assert get_sections(["§99.9"], conn=conn) == []


def test_format_sections_cites_by_id(conn):
    text = format_sections(get_sections(["§4.1"], conn=conn))
    assert text.startswith("[§4.1 ")
