"""Single-page web UI for the expense review agent.

Run with: python app.py
"""
import json
import logging
import re
from decimal import Decimal, InvalidOperation
from functools import lru_cache

from flask import Flask, render_template, request, send_file
from pypdf import PdfReader

from expense_agent.agent import review_expense
from expense_agent.config import AS_OF, ROOT, load_settings
from expense_agent.db import connect
from expense_agent.judge import SCORES, judge_and_record

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


@app.route("/policy")
def policy():
    return send_file(load_settings().policy_pdf, mimetype="application/pdf")


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
                try:
                    judgement = judge_and_record(review.decision_id, review.run_id, req,
                                                 review.retrieved_ids, review.tool_results,
                                                 review.decision, review.reason)
                except Exception as e:  # the decision stands; say the judge failed
                    error = f"Judge failed. {type(e).__name__}: {e}"

    employees, categories = load_choices()
    return render_template("index.html", form=form, employees=employees,
                           categories=categories, examples=load_examples(),
                           policy_pages=policy_pages(), scores=SCORES,
                           review=review, judgement=judgement, error=error)


@app.route("/admin")
def admin():
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            select d.created_at, d.employee_id, e.name, d.amount, d.category,
                   d.model_decision, d.final_decision, d.gate_overruled, d.gate_reason,
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
    overruled = sum(1 for r in rows if r["gate_overruled"])
    return render_template("admin.html", rows=rows, scores=SCORES, averages=averages,
                           overruled=overruled, policy_pages=policy_pages())


if __name__ == "__main__":
    app.run(port=5050, debug=True)
