"""Single-page web UI for the expense review agent.

    python app.py              # http://127.0.0.1:5050
    python app.py 8806 --debug # another port; --debug reloads on edits
"""
import json
import logging
import re
import sys
import threading
from decimal import Decimal, InvalidOperation
from functools import lru_cache

import markdown
from flask import Flask, redirect, render_template, request, send_file, url_for
from pypdf import PdfReader

from expense_agent.agent import Review, review_expense
from expense_agent.config import AS_OF, ROOT, load_settings
from expense_agent.db import connect
from expense_agent.judge import SCORES, Judgement, judge_and_record

logging.basicConfig(level=logging.INFO, format="%(name)s  %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

app = Flask(__name__)

ACRONYMS = {"vp": "VP"}


@app.template_filter("pretty")
def pretty(value):
    """Display label for a stored value: 'client_entertainment' -> 'Client entertainment'."""
    text = str(value).replace("_", " ")
    return ACRONYMS.get(text, text[:1].upper() + text[1:])

FIELDS = ["employee_id", "amount", "category", "transaction_date",
          "vendor", "approver_id", "notes", "receipt_text"]


def load_choices():
    with connect() as conn, conn.cursor() as cur:
        cur.execute("select employee_id, name, role, active from employees order by employee_id")
        employees = [dict(id=r[0], name=r[1], role=r[2], active=r[3]) for r in cur.fetchall()]
        cur.execute("select category from budget_categories order by category")
        categories = [r[0] for r in cur.fetchall()]
    return employees, categories


def load_examples():
    return json.loads((ROOT / "data" / "examples.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def policy_pages() -> dict[str, int]:
    """Which PDF page each section heading is on, so a citation can link to it."""
    pages = {}
    for number, page in enumerate(PdfReader(str(load_settings().policy_pdf)).pages, start=1):
        for section_id in re.findall(r"^(§\d+\.\d+) ", page.extract_text() or "", re.MULTILINE):
            pages.setdefault(section_id, number)
    return pages


@lru_cache(maxsize=1)
def readme_html() -> str:
    """The README, rendered under the tool so the page and the document cannot drift.
    Its own title is dropped because the page already has one."""
    text = (ROOT / "README.md").read_text(encoding="utf-8").split("\n", 1)[1]
    return markdown.markdown(text, extensions=["tables", "fenced_code", "toc"])


@app.route("/policy")
def policy():
    return send_file(load_settings().policy_pdf, mimetype="application/pdf")


def load_decision(decision_id: str):
    """A saved decision, its judge scores, and the form that produced it. None if unknown."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select d.request, d.final_decision, d.model_decision, d.reason, d.policy_ids,
                   d.retrieved_ids, d.tool_results, d.guard_overruled, d.guard_reason,
                   d.langsmith_run_id, d.trace_url,
                   j.grounded, j.complete, j.clear, j.safe, j.evidence, j.judge_model, j.judge_prompt
            from decisions d left join judgements j on j.decision_id = d.decision_id
            where d.decision_id::text = %s
            """,
            (decision_id,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    (req, decision, model_decision, reason, policy_ids, retrieved_ids, tool_results,
     overruled, guard_reason, run_id, trace_url, *scores) = row
    form = {f: str(req.get(f, "")) for f in FIELDS}
    review = Review(decision, reason, policy_ids, retrieved_ids,
                    [t["name"] for t in tool_results], tool_results, model_decision,
                    overruled, guard_reason, run_id, trace_url, decision_id)
    judgement = Judgement(*scores) if scores[0] is not None else None
    return form, review, judgement


def judge_later(*args) -> None:
    try:
        judge_and_record(*args)
    except Exception:  # the decision stands; the page keeps saying it is unscored
        app.logger.exception("judge failed")


@app.route("/", methods=["GET", "POST"])
def index():
    form = {f: "" for f in FIELDS}
    form["transaction_date"] = AS_OF.isoformat()
    review = judgement = error = None

    if request.method == "POST":
        form = {f: request.form.get(f, "").strip() for f in FIELDS}
        try:
            amount = Decimal(form["amount"])
            if amount < 0:
                raise InvalidOperation
        except InvalidOperation:
            error = "Amount must be a positive number."
        else:
            req = {**form, "amount": amount, "submitted_date": AS_OF.isoformat()}
            try:
                review = review_expense(req)
            except Exception as e:  # show the failure instead of hiding it
                error = f"{type(e).__name__}: {e}"
            else:
                # The judge takes a few seconds and is not part of the decision, so it
                # runs after the response. The decision page shows its scores once saved.
                threading.Thread(target=judge_later, daemon=True, args=(
                    review.decision_id, review.run_id, req, review.retrieved_ids,
                    review.tool_results, review.decision, review.reason)).start()
                # Show the saved decision at its own URL, so a refresh does not resubmit.
                return redirect(url_for("index", decision=review.decision_id))
    elif request.args.get("decision"):
        saved = load_decision(request.args["decision"])
        if saved:
            form, review, judgement = saved

    employees, categories = load_choices()
    return render_template("index.html", form=form, employees=employees,
                           categories=categories, examples=load_examples(),
                           policy_pages=policy_pages(), scores=SCORES, readme=readme_html(),
                           review=review, judgement=judgement, error=error)


@app.route("/admin")
def admin():
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select d.created_at, d.employee_id, e.name, d.amount, d.category,
                   d.model_decision, d.final_decision, d.guard_overruled, d.guard_reason,
                   d.policy_ids, d.reason, d.trace_url, d.model, d.prompt_commit,
                   j.grounded, j.complete, j.clear, j.safe, j.evidence, j.judge_model
            from decisions d
            join employees e on e.employee_id = d.employee_id
            left join judgements j on j.decision_id = d.decision_id
            order by d.created_at desc
            limit 200
            """
        )
        columns = [c.name for c in cur.description]
        rows = [dict(zip(columns, r)) for r in cur.fetchall()]
    averages = {k: (sum(r[k] for r in rows if r[k]) / max(1, sum(1 for r in rows if r[k])))
                for k in SCORES}
    overruled = sum(1 for r in rows if r["guard_overruled"])
    return render_template("admin.html", rows=rows, scores=SCORES, averages=averages,
                           overruled=overruled, policy_pages=policy_pages())


if __name__ == "__main__":
    ports = [int(a) for a in sys.argv[1:] if a.isdigit()]
    app.run(port=ports[0] if ports else 5050, debug="--debug" in sys.argv)
