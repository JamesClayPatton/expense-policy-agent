"""The review graph, from the request to the recorded decision.

    retrieve ──▶ agent ──▶ tools ──▶ agent ... ──▶ guard ──▶ record ──▶ END

The model ends its loop by calling the Decision tool, so the decision costs no
extra model call. The guard then checks the hard rules in code, and record
writes the audit row, the one write in the system. Eval runs skip record.

`python -m expense_agent.graph` prints the graph as Mermaid.
"""
import json
import logging
import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langsmith import Client
from pydantic import BaseModel, Field

from expense_agent import retrieval, tools
from expense_agent.config import AS_OF, load_settings
from expense_agent.guard import Verdict, guard as apply_guard
from expense_agent.traces import public_trace_url

MAX_TOOL_ROUNDS = 6
log = logging.getLogger("expense_agent")


# --- Tools, as the model sees them -------------------------------------------

def _plain(value):
    """Tool results go back to the model as JSON."""
    return json.dumps(value, default=str, indent=1)


@tool
def get_employee(employee_id: str) -> str:
    """Look up an employee: name, role, department, manager, whether they are
    active, and the maximum amount their role may approve."""
    return _plain(tools.get_employee(employee_id))


@tool
def get_budget_remaining(employee_id: str, category: str, transaction_date: str) -> str:
    """How much of the employee's monthly budget for a category is left in the
    month of the transaction date (YYYY-MM-DD). Counts pending, approved and
    paid claims. Categories with no budget return remaining as null."""
    return _plain(tools.get_budget_remaining(employee_id, category,
                                             date.fromisoformat(transaction_date)))


@tool
def find_duplicates(employee_id: str, vendor: str, amount: str, transaction_date: str) -> str:
    """Earlier claims by the same employee at the same vendor within seven days
    of the transaction date (YYYY-MM-DD). Each match is labelled exact_duplicate,
    near_duplicate (same amount) or same_vendor (different amount, possible
    split purchase), with its status."""
    return _plain(tools.find_duplicates(employee_id, vendor, Decimal(amount),
                                        date.fromisoformat(transaction_date)))


TOOLS = [get_employee, get_budget_remaining, find_duplicates]


# --- The decision the model must return ---------------------------------------

class Decision(BaseModel):
    """Your final decision on the expense. Call this once, when you are done checking."""
    decision: Literal["approve", "flag", "reject"]
    policy_ids: list[str] = Field(description="Policy section IDs relied on, e.g. ['§4.2']")
    reason: str = Field(description="Two or three plain sentences explaining the decision")


# --- Graph state --------------------------------------------------------------

class State(TypedDict):
    # Set by the caller.
    request: dict
    record: bool                    # write the audit row; eval runs pass False
    run_id: str | None              # the LangSmith trace this review belongs to
    trace_url: str | None           # private link in; record swaps in a public one
    # Written by the nodes, one writer each.
    sections: list[retrieval.Section]                    # retrieve
    messages: Annotated[list[AnyMessage], add_messages]  # retrieve, agent, tools
    decision: Decision | None       # agent: what the model said
    verdict: Verdict | None         # guard: what stands
    decision_id: str | None         # record: the audit row


def initial_state(request: dict, record: bool = True, run_id: str | None = None,
                  trace_url: str | None = None) -> State:
    return {"request": request, "record": record, "run_id": run_id, "trace_url": trace_url,
            "sections": [], "messages": [], "decision": None, "verdict": None,
            "decision_id": None}


def format_request(r: dict) -> str:
    approver = r.get("approver_id") or "none"
    return "\n".join([
        f"Employee ID: {r['employee_id']}",
        f"Amount: ${Decimal(r['amount']):.2f}",
        f"Category: {r['category']}",
        f"Transaction date: {r['transaction_date']}",
        f"Submitted: {r.get('submitted_date', AS_OF.isoformat())}",
        f"Vendor: {r['vendor']}",
        f"Approval on record: {approver}",
        f"Employee notes: {r.get('notes') or '(none)'}",
        f"Receipt text: {r.get('receipt_text') or '(none)'}",
    ])


def tool_results(messages: list[AnyMessage]) -> list[dict]:
    return [{"name": m.name, "content": json.loads(m.content)}
            for m in messages if isinstance(m, ToolMessage)]


# --- Routing ------------------------------------------------------------------

def after_agent(state: State) -> Literal["lookup", "decided"]:
    """The model either asked for lookups or called Decision."""
    return "decided" if state["decision"] is not None else "lookup"


def after_guard(state: State) -> Literal["live run", "eval run"]:
    """Eval runs score the decision without writing it."""
    return "live run" if state["record"] else "eval run"


def wire(retrieve, agent, tools_node, guard, record) -> StateGraph:
    """The shape of the graph, separate from what each node does, so the shape
    can be drawn and tested without a database or a model."""
    g = StateGraph(State)
    g.add_node("retrieve", retrieve)
    g.add_node("agent", agent)
    g.add_node("tools", tools_node)
    g.add_node("guard", guard)
    g.add_node("record", record)
    g.set_entry_point("retrieve")
    g.add_edge("retrieve", "agent")
    g.add_conditional_edges("agent", after_agent, {"lookup": "tools", "decided": "guard"})
    g.add_edge("tools", "agent")
    g.add_conditional_edges("guard", after_guard, {"live run": "record", "eval run": END})
    g.add_edge("record", END)
    return g


def mermaid() -> str:
    """The graph as Mermaid, drawn from the same wiring the agent runs."""
    def stub(state):
        return {}
    return wire(stub, stub, stub, stub, stub).compile().get_graph().draw_mermaid()


# --- The real nodes -----------------------------------------------------------

def build_graph():
    settings = load_settings()
    prompt = Client().pull_prompt(settings.agent_prompt)
    prompt_commit = prompt.metadata.get("lc_hub_commit_hash", "")
    # Reasoning effort is the main speed knob: most of a review is the model thinking.
    llm = ChatOpenAI(model=settings.agent_model,
                     **({"reasoning_effort": settings.agent_reasoning} if settings.agent_reasoning else {}))
    # The model must call a tool every turn: a lookup, or Decision to finish.
    with_tools = llm.bind_tools(TOOLS + [Decision], tool_choice="required")

    def retrieve(state: State):
        sections = retrieval.retrieve(state["request"])
        log.info("retrieved %s", ", ".join(f"{s.section_id} ({s.score:.2f})" if s.score is not None
                                           else s.section_id for s in sections))
        messages = prompt.format_messages(
            today=AS_OF.isoformat(),
            policy_sections=retrieval.format_sections(sections),
            request=format_request(state["request"]),
        )
        return {"sections": sections, "messages": messages}

    def agent(state: State):
        rounds = sum(isinstance(m, AIMessage) for m in state["messages"])
        messages = state["messages"]
        if rounds >= MAX_TOOL_ROUNDS:
            messages = messages + [HumanMessage("You have checked enough. Call Decision now.")]
        reply = with_tools.invoke(messages)
        decision = None
        for call in reply.tool_calls:
            if call["name"] == "Decision":
                decision = Decision(**call["args"])
                log.info("decision %s citing %s", decision.decision, ", ".join(decision.policy_ids))
            else:
                log.info("tool %s(%s)", call["name"], json.dumps(call["args"]))
        return {"messages": [reply], "decision": decision}

    def guard(state: State):
        # Looks the facts up itself; never reads the model's transcript.
        d = state["decision"]
        verdict = apply_guard(d.decision, state["request"], d.policy_ids)
        if verdict.overruled:
            log.info("guard overruled %s: %s", d.decision, verdict.reason)
        return {"verdict": verdict}

    def record(state: State):
        request, d, v = state["request"], state["decision"], state["verdict"]
        trace_url = state["trace_url"]
        if settings.public_traces and state["run_id"]:
            trace_url = public_trace_url(state["run_id"]) or trace_url
        row = tools.record_decision(
            request_id=str(uuid.uuid4()), employee_id=request["employee_id"],
            amount=request["amount"], category=request["category"],
            model_decision=d.decision, final_decision=v.decision,
            reason=d.reason, policy_ids=d.policy_ids,
            guard_overruled=v.overruled, guard_reason=v.reason,
            langsmith_run_id=state["run_id"],
            request=request, retrieved_ids=[s.section_id for s in state["sections"]],
            tool_results=tool_results(state["messages"]), prompt_commit=prompt_commit,
            model=settings.agent_model, trace_url=trace_url,
        )
        return {"decision_id": row["decision_id"], "trace_url": trace_url}

    graph = wire(retrieve, agent, ToolNode(TOOLS), guard, record).compile()
    return graph, prompt_commit


if __name__ == "__main__":
    print(mermaid())
