"""The expense review agent. The web page calls review_expense() and nothing else."""
import logging
from dataclasses import dataclass, field
from functools import lru_cache

from langchain_core.messages import AIMessage
from langsmith import traceable
from langsmith.run_helpers import get_current_run_tree

from expense_agent.config import load_settings
from expense_agent.db import connect, sharing
from expense_agent.graph import build_graph, initial_state, tool_results
from expense_agent.traces import public_trace_url  # noqa: F401  (re-exported for callers)

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


def review_expense(request: dict, record: bool = True) -> Review:
    """Review one expense. The graph records the decision in the audit table
    unless told not to (eval runs pass record=False)."""
    graph, prompt_commit = _graph()
    settings = load_settings()
    return _review(request, record, graph, {"prompt": settings.agent_prompt,
                                            "prompt_commit": prompt_commit,
                                            "model": settings.agent_model})


@traceable(name="review_expense", run_type="chain")
def _review(request: dict, record: bool, graph, metadata: dict) -> Review:
    run = get_current_run_tree()
    if run is not None:
        run.metadata.update(metadata)
    run_id = str(run.id) if run is not None else None

    # One connection for the retrieval, the tools, the guard and the write. It
    # commits when the block ends.
    with connect() as conn, sharing(conn):
        state = graph.invoke(initial_state(
            request, record=record, run_id=run_id,
            trace_url=run.get_url() if run is not None else None))

    d, v = state["decision"], state["verdict"]
    tools_called = [c["name"] for m in state["messages"]
                    if isinstance(m, AIMessage) for c in m.tool_calls if c["name"] != "Decision"]
    return Review(
        decision=v.decision,
        reason=d.reason,
        policy_ids=d.policy_ids,
        retrieved_ids=[s.section_id for s in state["sections"]],
        tools_called=tools_called,
        tool_results=tool_results(state["messages"]),
        model_decision=d.decision,
        guard_overruled=v.overruled,
        guard_reason=v.reason,
        run_id=run_id,
        trace_url=state["trace_url"],
        decision_id=state["decision_id"],
    )
