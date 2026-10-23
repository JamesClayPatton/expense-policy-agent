"""The expense review agent. The web page calls review_expense() and nothing else."""
import json
import uuid
from dataclasses import dataclass, field
from functools import lru_cache

from langchain_core.messages import AIMessage, ToolMessage
from langsmith import traceable
from langsmith.run_helpers import get_current_run_tree

from expense_agent.config import load_settings
from expense_agent.graph import build_graph
from expense_agent.tools import record_decision


@dataclass
class Review:
    decision: str                       # approve | flag | reject
    reason: str
    policy_ids: list[str] = field(default_factory=list)
    retrieved_ids: list[str] = field(default_factory=list)
    tools_called: list[str] = field(default_factory=list)
    tool_results: list[dict] = field(default_factory=list)
    gate_overruled: bool = False
    run_id: str | None = None           # LangSmith trace
    trace_url: str | None = None
    decision_id: str | None = None      # audit row, when recorded


@lru_cache(maxsize=1)
def _graph():
    return build_graph()


def review_expense(request: dict, record: bool = True) -> Review:
    """Review one expense. Records the decision in the audit table unless told not
    to (eval runs pass record=False)."""
    graph, prompt_commit = _graph()
    settings = load_settings()
    review = _review(request, graph, {"prompt": settings.agent_prompt,
                                      "prompt_commit": prompt_commit,
                                      "model": settings.agent_model})
    if record:
        row = record_decision(
            request_id=str(uuid.uuid4()), employee_id=request["employee_id"],
            amount=request["amount"], category=request["category"],
            model_decision=review.decision, final_decision=review.decision,
            reason=review.reason, policy_ids=review.policy_ids,
            gate_overruled=review.gate_overruled, langsmith_run_id=review.run_id,
            request=request, retrieved_ids=review.retrieved_ids,
            tool_results=review.tool_results, prompt_commit=prompt_commit,
            model=settings.agent_model,
        )
        review.decision_id = row["decision_id"]
    return review


@traceable(name="review_expense", run_type="chain")
def _review(request: dict, graph, metadata: dict) -> Review:
    run = get_current_run_tree()
    if run is not None:
        run.metadata.update(metadata)

    state = graph.invoke({"request": request, "messages": [], "decision": None})
    d = state["decision"]
    tools_called = [c["name"] for m in state["messages"]
                    if isinstance(m, AIMessage) for c in m.tool_calls if c["name"] != "Decision"]
    tool_results = [{"name": m.name, "content": json.loads(m.content)}
                    for m in state["messages"] if isinstance(m, ToolMessage)]
    return Review(
        decision=d.decision,
        reason=d.reason,
        policy_ids=d.policy_ids,
        retrieved_ids=[s.section_id for s in state["sections"]],
        tools_called=tools_called,
        tool_results=tool_results,
        run_id=str(run.id) if run is not None else None,
        trace_url=run.get_url() if run is not None else None,
    )
