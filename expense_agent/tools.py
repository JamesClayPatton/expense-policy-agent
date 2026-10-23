"""The agent's tools. Three reads and one write, all against the expense database.

Each function takes an optional open connection so one review can share a
single connection (and a test can roll everything back). Amounts are Decimals.
"""
import json
from datetime import date, timedelta
from decimal import Decimal

import psycopg
from psycopg.types.json import Jsonb

from expense_agent.config import AS_OF
from expense_agent.db import connect

# Spending in these states counts toward the monthly budget (policy 8.1).
COUNTED_STATUSES = ("pending", "approved", "paid")

# Policy 2.3: a repeat within this many days of the same vendor and amount is a
# potential duplicate. Policy 2.2 uses the same window for split purchases.
WINDOW_DAYS = 7


def _dumps(value) -> str:
    return json.dumps(value, default=str)


def _run(conn, fn):
    if conn is not None:
        return fn(conn)
    with connect() as own:
        return fn(own)


def get_employee(employee_id: str, conn: psycopg.Connection | None = None) -> dict:
    """Who the employee is, whether they are active, and how much they may approve."""
    def query(c):
        with c.cursor() as cur:
            cur.execute(
                """
                select e.employee_id, e.name, e.role, e.department, e.manager_id,
                       e.active, r.max_approval
                from employees e join roles r on r.role = e.role
                where e.employee_id = %s
                """,
                (employee_id,),
            )
            row = cur.fetchone()
        if row is None:
            return {"found": False, "employee_id": employee_id}
        return {
            "found": True,
            "employee_id": row[0],
            "name": row[1],
            "role": row[2],
            "department": row[3],
            "manager_id": row[4],
            "active": row[5],
            "max_approval": row[6],
        }
    return _run(conn, query)


def get_budget_remaining(employee_id: str, category: str, month_of: date = AS_OF,
                         conn: psycopg.Connection | None = None) -> dict:
    """How much of the employee's monthly budget for a category is left.

    Spending is counted for the calendar month containing `month_of`, which should
    be the transaction date of the expense under review. A category with no limit
    (for example 'other') returns limit and remaining as None.
    """
    start = month_of.replace(day=1)
    end = (start + timedelta(days=32)).replace(day=1)

    def query(c):
        with c.cursor() as cur:
            cur.execute("select default_monthly_limit from budget_categories where category = %s",
                        (category,))
            row = cur.fetchone()
            if row is None:
                return {"found": False, "category": category}
            limit = row[0]
            cur.execute(
                """
                select coalesce(sum(amount), 0)
                from expenses
                where employee_id = %s and category = %s
                  and status = any(%s)
                  and transaction_date >= %s and transaction_date < %s
                """,
                (employee_id, category, list(COUNTED_STATUSES), start, end),
            )
            spent = cur.fetchone()[0]
        return {
            "found": True,
            "employee_id": employee_id,
            "category": category,
            "month": start.strftime("%Y-%m"),
            "monthly_limit": limit,
            "spent": spent,
            "remaining": None if limit is None else limit - spent,
        }
    return _run(conn, query)


def find_duplicates(employee_id: str, vendor: str, amount: Decimal, transaction_date: date,
                    conn: psycopg.Connection | None = None) -> dict:
    """Earlier claims by the same employee at the same vendor within seven days.

    Each match is labelled:
      exact_duplicate  same amount and same transaction date (policy 2.3)
      near_duplicate   same amount within seven days (policy 2.3)
      same_vendor      different amount within seven days; a possible split (policy 2.2)
    """
    amount = Decimal(amount)
    lo = transaction_date - timedelta(days=WINDOW_DAYS)
    hi = transaction_date + timedelta(days=WINDOW_DAYS)

    def query(c):
        with c.cursor() as cur:
            cur.execute(
                """
                select expense_id, transaction_date, submitted_date, vendor, amount,
                       category, status
                from expenses
                where employee_id = %s
                  and lower(trim(vendor)) = lower(trim(%s))
                  and transaction_date between %s and %s
                order by transaction_date
                """,
                (employee_id, vendor, lo, hi),
            )
            rows = cur.fetchall()
        matches = []
        for r in rows:
            if r[4] == amount and r[1] == transaction_date:
                kind = "exact_duplicate"
            elif r[4] == amount:
                kind = "near_duplicate"
            else:
                kind = "same_vendor"
            matches.append({
                "expense_id": r[0],
                "transaction_date": r[1],
                "submitted_date": r[2],
                "vendor": r[3],
                "amount": r[4],
                "category": r[5],
                "status": r[6],
                "kind": kind,
            })
        return {"employee_id": employee_id, "vendor": vendor, "amount": amount,
                "window_days": WINDOW_DAYS, "matches": matches}
    return _run(conn, query)


def record_decision(request_id: str, employee_id: str, amount: Decimal, category: str,
                    model_decision: str, final_decision: str, reason: str,
                    policy_ids: list[str], gate_overruled: bool = False,
                    gate_reason: str | None = None, langsmith_run_id: str | None = None,
                    request: dict | None = None, retrieved_ids: list[str] = (),
                    tool_results: list | None = None, prompt_commit: str | None = None,
                    model: str | None = None, trace_url: str | None = None,
                    conn: psycopg.Connection | None = None) -> dict:
    """Write the decision to the audit log. The only write the agent can make.

    Besides the decision itself it keeps what the agent saw (request, retrieved
    sections, tool results) so a judge can grade the decision afterwards.
    """
    def query(c):
        with c.cursor() as cur:
            cur.execute(
                """
                insert into decisions (request_id, employee_id, amount, category,
                    model_decision, final_decision, gate_overruled, gate_reason,
                    policy_ids, reason, langsmith_run_id,
                    request, retrieved_ids, tool_results, prompt_commit, model, trace_url)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                returning decision_id, created_at
                """,
                (request_id, employee_id, Decimal(amount), category, model_decision,
                 final_decision, gate_overruled, gate_reason, policy_ids, reason,
                 langsmith_run_id,
                 Jsonb(request, dumps=_dumps), list(retrieved_ids),
                 Jsonb(tool_results, dumps=_dumps), prompt_commit, model, trace_url),
            )
            decision_id, created_at = cur.fetchone()
        c.commit()
        return {"decision_id": str(decision_id), "created_at": created_at}
    return _run(conn, query)
