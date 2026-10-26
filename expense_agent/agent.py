"""The expense review agent. The web page calls review_expense() and nothing else."""
import json
import logging
import uuid
from dataclasses import dataclass, field
from functools import lru_cache

from langchain_core.messages import AIMessage, ToolMessage
from langsmith import Client, traceable
from langsmith.run_helpers import get_current_run_tree
from langsmith.utils import LangSmithError

from expense_agent.config import load_settings
from expense_agent.db import connect, sharing
from expense_agent.graph import build_graph
from expense_agent.guard import guard
from expense_agent.tools import record_decision

log = logging.getLogger("expense_agent")


@dataclass
class Review:
    decision: str                       # approve | flag | reject, after the guard
    reason: str
    policy_ids: list[str] = field(default_factory=list)
    retrieved_ids: list[str] = field(default_factory=list)
    tools_called: list[str] = field(default_factory=list)
    tool_results: list[dict] = field(default_factory=list)
    model_decision: str = ""            # what the model said before the guard
    guard_overruled: bool = False
    guard_reason: str | None = None
    run_id: str | None = None           # LangSmith trace
    trace_url: str | None = None
    decision_id: str | None = None      # audit row, when recorded


@lru_cache(maxsize=1)
def _graph():
    return build_graph()


def public_trace_url(run_id: str) -> str | None:
    """A share link for the trace that needs no LangSmith account. None when
    LangSmith does not have the run, for example once the account's trace limit
    is hit, so the caller can fall back to the private link."""
    client = Client()
    try:
        return client.read_run_shared_link(run_id) or client.share_run(run_id)
    except LangSmithError as e:
        log.warning("could not share trace %s: %s", run_id, e)
        return None


def review_expense(request: dict, record: bool = True) -> Review:
    """Review one expense. Records the decision in the audit table unless told not
    to (eval runs pass record=False)."""
    graph, prompt_commit = _graph()
    settings = load_settings()
    review = _review(request, graph, {"prompt": settings.agent_prompt,
                                      "prompt_commit": prompt_commit,
                                      "model": settings.agent_model})
    if record:
        if settings.public_traces and review.run_id:
            review.trace_url = public_trace_url(review.run_id) or review.trace_url
        row = record_decision(
            request_id=str(uuid.uuid4()), employee_id=request["employee_id"],
            amount=request["amount"], category=request["category"],
            model_decision=review.model_decision, final_decision=review.decision,
            reason=review.reason, policy_ids=review.policy_ids,
            guard_overruled=review.guard_overruled, guard_reason=review.guard_reason,
            langsmith_run_id=review.run_id,
            request=request, retrieved_ids=review.retrieved_ids,
            tool_results=review.tool_results, prompt_commit=prompt_commit,
            model=settings.agent_model, trace_url=review.trace_url,
        )
        review.decision_id = row["decision_id"]
    return review


@traceable(name="review_expense", run_type="chain")
def _review(request: dict, graph, metadata: dict) -> Review:
    run = get_current_run_tree()
    if run is not None:
        run.metadata.update(metadata)

    # One connection for the retrieval, the tools and the guard.
    with connect() as conn, sharing(conn):
        state = graph.invoke({"request": request, "messages": [], "decision": None})
        d = state["decision"]
        verdict = guard(d.decision, request, d.policy_ids, conn=conn)
    if verdict.overruled:
        log.info("guard overruled %s: %s", d.decision, verdict.reason)
    tools_called = [c["name"] for m in state["messages"]
                    if isinstance(m, AIMessage) for c in m.tool_calls if c["name"] != "Decision"]
    tool_results = [{"name": m.name, "content": json.loads(m.content)}
                    for m in state["messages"] if isinstance(m, ToolMessage)]
    return Review(
        decision=verdict.decision,
        reason=d.reason,
        policy_ids=d.policy_ids,
        retrieved_ids=[s.section_id for s in state["sections"]],
        tools_called=tools_called,
        tool_results=tool_results,
        model_decision=d.decision,
        guard_overruled=verdict.overruled,
        guard_reason=verdict.reason,
        run_id=str(run.id) if run is not None else None,
        trace_url=run.get_url() if run is not None else None,
    )
