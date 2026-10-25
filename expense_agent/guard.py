"""The guard: hard rules in plain code, applied after the model decides.

It can only make a decision stricter. An approve that breaks a rule becomes a
flag, with the broken rules recorded. It never changes a flag or a reject, and
it never touches the model's reason.

The guard looks the facts up itself rather than reading the model's transcript,
so it holds even when the model skipped a tool. It stays out of anything that
needs judgement (a possible split purchase, a missing receipt): those are the
model's job, and the evals measure how well it does them.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import psycopg
from langsmith import traceable

from expense_agent import retrieval, tools
from expense_agent.config import AS_OF

NO_APPROVAL_LIMIT = Decimal("250")   # policy 1.2: above this, approval is required
SUBMISSION_DAYS = 30                 # policy 1.1: later than this needs a human


@dataclass
class Verdict:
    decision: str
    overruled: bool = False
    reason: str | None = None


def check(request: dict, policy_ids: list[str], conn: psycopg.Connection | None = None) -> list[str]:
    """Every hard rule an approval of this request would break. Empty means none."""
    amount = Decimal(request["amount"])
    employee_id = request["employee_id"]
    approver_id = request.get("approver_id") or None
    when = date.fromisoformat(str(request["transaction_date"]))
    reasons = []

    # An approval must rest on policy that exists.
    if not policy_ids:
        reasons.append("no policy section cited")
    else:
        found = {s.section_id for s in retrieval.get_sections(policy_ids, conn=conn)}
        reasons += [f"cited section {id} does not exist" for id in policy_ids if id not in found]

    # Submission deadline (policy 1.1). Date arithmetic is not the model's strength.
    submitted = date.fromisoformat(str(request.get("submitted_date") or AS_OF))
    if (submitted - when).days > SUBMISSION_DAYS:
        reasons.append(f"submitted {(submitted - when).days} days after the transaction, "
                       f"over the {SUBMISSION_DAYS}-day limit (§1.1)")

    employee = tools.get_employee(employee_id, conn=conn)
    if not employee["found"]:
        reasons.append(f"employee {employee_id} not found")
    elif not employee["active"]:
        reasons.append(f"employee {employee_id} is not active (§1.1)")

    # Approval authority (policy 1.2 and 2.1).
    if approver_id is None:
        if amount > NO_APPROVAL_LIMIT:
            reasons.append(f"${amount:.2f} is over ${NO_APPROVAL_LIMIT} with no approval on record (§1.2)")
    elif approver_id == employee_id:
        reasons.append("approver is the claimant (§2.1)")
    else:
        approver = tools.get_employee(approver_id, conn=conn)
        if not approver["found"]:
            reasons.append(f"approver {approver_id} not found")
        elif approver["max_approval"] < amount:
            reasons.append(f"approver {approver_id} ({approver['role']}) may approve up to "
                           f"${approver['max_approval']:.2f}, not ${amount:.2f} (§2.1)")

    # The same expense twice (policy 2.3).
    for m in tools.find_duplicates(employee_id, request["vendor"], amount, when, conn=conn)["matches"]:
        if m["kind"] in ("exact_duplicate", "near_duplicate"):
            reasons.append(f"{m['kind'].replace('_', ' ')} of {m['expense_id']} ({m['status']}) (§2.3)")

    # Monthly category budget (policy 8.1). Over budget always goes to a human,
    # because the Director approval it needs must predate the purchase and the
    # guard cannot see when an approval was given.
    budget = tools.get_budget_remaining(employee_id, request["category"], when, conn=conn)
    if budget["found"] and budget["remaining"] is not None and budget["remaining"] < amount:
        reasons.append(f"{request['category']} budget has ${budget['remaining']:.2f} left this month, "
                       f"not ${amount:.2f} (§8.1)")

    return reasons


@traceable(name="guard", run_type="chain")
def guard(decision: str, request: dict, policy_ids: list[str],
          conn: psycopg.Connection | None = None) -> Verdict:
    """Apply the hard rules to the model's decision. Only an approve can change."""
    if decision != "approve":
        return Verdict(decision)
    reasons = check(request, policy_ids, conn=conn)
    if reasons:
        return Verdict("flag", overruled=True, reason="; ".join(reasons))
    return Verdict("approve")
