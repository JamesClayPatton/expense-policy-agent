"""The web page's routes. The agent itself is not run here."""
from decimal import Decimal

import pytest

import app as web
from expense_agent.agent import Review
from expense_agent.db import connect
from expense_agent.judge import SCORES, Judgement, record_judgement
from expense_agent.tools import record_decision

REQUEST = {"employee_id": "E019", "amount": "28.50", "category": "meals",
           "transaction_date": "2026-10-21", "submitted_date": "2026-10-22",
           "vendor": "Corner Deli", "approver_id": "E011",
           "notes": "Lunch.", "receipt_text": "Corner Deli. Total 28.50."}


@pytest.fixture
def client():
    web.app.config["TESTING"] = True
    return web.app.test_client()


@pytest.fixture
def saved():
    """A recorded decision with judge scores, deleted afterwards."""
    row = record_decision(request_id="test", employee_id="E019", amount=Decimal("28.50"),
                          category="meals", model_decision="approve", final_decision="approve",
                          reason="Under the $35 lunch limit.", policy_ids=["§4.1"],
                          request=REQUEST, retrieved_ids=["§4.1"],
                          tool_results=[{"name": "get_employee", "content": {}}],
                          trace_url="https://smith.langchain.com/x")
    record_judgement(row["decision_id"],
                     Judgement(5, 4, 4, 5, {k: "ok" for k in SCORES}, "test", "test"))
    yield row["decision_id"]
    with connect() as c, c.cursor() as cur:
        cur.execute("delete from decisions where decision_id = %s", (row["decision_id"],))


def test_a_review_redirects_to_the_saved_decision_so_refresh_does_not_resubmit(client, monkeypatch):
    monkeypatch.setattr(web, "review_expense", lambda req: Review("approve", "ok", decision_id="abc"))
    monkeypatch.setattr(web, "judge_and_record", lambda *a: None)
    resp = client.post("/", data=REQUEST)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/?decision=abc")


def test_the_judge_runs_after_the_decision_is_shown(client, monkeypatch):
    """The judge takes seconds and is not part of the decision, so the page does not wait."""
    import threading
    import time
    judged = threading.Event()

    def slow_judge(*a):
        time.sleep(1.5)
        judged.set()

    monkeypatch.setattr(web, "review_expense", lambda req: Review("approve", "ok", decision_id="abc"))
    monkeypatch.setattr(web, "judge_and_record", slow_judge)
    start = time.monotonic()
    assert client.post("/", data=REQUEST).status_code == 302
    assert time.monotonic() - start < 1.0
    assert judged.wait(5)


def test_a_saved_decision_renders_without_running_the_agent(client, saved, monkeypatch):
    monkeypatch.setattr(web, "review_expense", lambda *a, **k: pytest.fail("the agent ran"))
    html = client.get(f"/?decision={saved}").get_data(as_text=True)
    assert "Approve" in html and "Under the $35 lunch limit." in html
    assert 'value="Corner Deli"' in html          # the form shows what was submitted
    assert "Get employee" in html                  # checks run, from the stored tool results
    assert "Grounded <b>5</b>" in html


def test_an_unknown_decision_id_shows_a_blank_page(client):
    html = client.get("/?decision=00000000-0000-0000-0000-000000000000").get_data(as_text=True)
    assert "Submit an expense to see the decision here." in html


def test_the_readme_is_rendered_under_the_tool(client):
    html = client.get("/").get_data(as_text=True)
    assert "<h2 id=\"quickstart\">Quickstart</h2>" in html
    assert "<table>" in html                       # the results tables came through
    assert 'src="static/architecture.svg"' in html
    for anchor in ("quickstart", "how-it-works", "traces-and-evaluation-runs", "before-and-after",
                   "compared-with-simpler-designs", "one-complete-run-reproducible",
                   "dataset-and-evaluators", "known-failures-and-limitations",
                   "evaluating-this-in-production"):
        assert f'href="#{anchor}"' in html and f'id="{anchor}"' in html   # every card lands
    assert html.count("Expense Policy Agent</h1>") == 1   # the README's own title is dropped


def test_a_bad_amount_shows_an_error_without_a_redirect(client):
    resp = client.post("/", data={**REQUEST, "amount": "lots"})
    assert resp.status_code == 200
    assert "Amount must be a positive number." in resp.get_data(as_text=True)
