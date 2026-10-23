"""The review graph: retrieve policy -> agent with tools -> structured decision.

    retrieve ──▶ agent ──▶ tools ──▶ agent ... ──▶ END

The model ends the loop by calling the Decision tool, so the decision costs no
extra model call.
"""
import json
import logging
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langsmith import Client
from pydantic import BaseModel, Field

from expense_agent import retrieval, tools
from expense_agent.config import AS_OF, load_settings

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
    request: dict
    sections: list[retrieval.Section]
    messages: Annotated[list[AnyMessage], add_messages]
    decision: Decision | None


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


def build_graph():
    settings = load_settings()
    prompt = Client().pull_prompt(settings.agent_prompt)
    llm = ChatOpenAI(model=settings.agent_model)
    # The model must call a tool every turn: a lookup, or Decision to finish.
    with_tools = llm.bind_tools(TOOLS + [Decision], tool_choice="required")

    def retrieve(state: State):
        sections = retrieval.search(retrieval.query_text(state["request"]))
        log.info("retrieved %s", ", ".join(f"{s.section_id} ({s.score:.2f})" for s in sections))
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

    def after_agent(state: State) -> Literal["tools", "__end__"]:
        return END if state["decision"] is not None else "tools"

    g = StateGraph(State)
    g.add_node("retrieve", retrieve)
    g.add_node("agent", agent)
    g.add_node("tools", ToolNode(TOOLS))
    g.set_entry_point("retrieve")
    g.add_edge("retrieve", "agent")
    g.add_conditional_edges("agent", after_agent)
    g.add_edge("tools", "agent")
    return g.compile(), prompt.metadata.get("lc_hub_commit_hash", "")
