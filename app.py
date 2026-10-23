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
    review = notice = error = None

    if request.method == "POST":
        form = {f: request.form.get(f, "").strip() for f in FIELDS}
        try:
            amount = Decimal(form["amount"])
            if amount < 0:
                raise InvalidOperation
        except InvalidOperation:
            error = "Amount must be a positive number."
        else:
            try:
                review = review_expense({**form, "amount": amount, "submitted_date": AS_OF.isoformat()})
            except NotImplementedError as e:
                notice = str(e)
            except Exception as e:  # show the failure instead of hiding it
                error = f"{type(e).__name__}: {e}"

    employees, categories = load_choices()
    return render_template("index.html", form=form, employees=employees,
                           categories=categories, examples=load_examples(),
                           policy_pages=policy_pages(),
                           review=review, notice=notice, error=error)


if __name__ == "__main__":
    app.run(port=5050, debug=True)
