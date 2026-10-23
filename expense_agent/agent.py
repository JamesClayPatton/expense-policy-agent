"""The expense review agent. The web page calls review_expense() and nothing else."""
from dataclasses import dataclass, field
from functools import lru_cache

from langchain_core.messages import AIMessage
from langsmith import traceable
from langsmith.run_helpers import get_current_run_tree

from expense_agent.config import load_settings
from expense_agent.graph import build_graph


@dataclass
class Review:
    decision: str                       # approve | flag | reject
    reason: str
    policy_ids: list[str] = field(default_factory=list)
    retrieved_ids: list[str] = field(default_factory=list)
    tools_called: list[str] = field(default_factory=list)
    gate_overruled: bool = False
    trace_url: str | None = None


@lru_cache(maxsize=1)
def _graph():
    return build_graph()


def review_expense(request: dict) -> Review:
    graph, prompt_commit = _graph()
    settings = load_settings()
    return _review(request, graph, {"prompt": settings.agent_prompt,
                                    "prompt_commit": prompt_commit,
                                    "model": settings.agent_model})


@traceable(name="review_expense", run_type="chain")
def _review(request: dict, graph, metadata: dict) -> Review:
    run = get_current_run_tree()
    if run is not None:
        run.metadata.update(metadata)

    state = graph.invoke({"request": request, "messages": [], "decision": None})
    d = state["decision"]
    tools_called = [c["name"] for m in state["messages"]
                    if isinstance(m, AIMessage) for c in m.tool_calls if c["name"] != "Decision"]
    return Review(
        decision=d.decision,
        reason=d.reason,
        policy_ids=d.policy_ids,
        retrieved_ids=[s.section_id for s in state["sections"]],
        tools_called=tools_called,
        trace_url=run.get_url() if run is not None else None,
    )
