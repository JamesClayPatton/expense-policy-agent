"""The graph's shape and routing, with stub nodes: no database, no model."""
from expense_agent.graph import Decision, initial_state, mermaid, wire
from expense_agent.guard import Verdict


def run_with_stubs(record: bool, lookups: int = 1) -> list[str]:
    """Run the real wiring with stub nodes and return the order they ran in."""
    visited = []

    def node(name, update=lambda s: {}):
        def fn(state):
            visited.append(name)
            return update(state)
        return fn

    def agent(state):
        done = visited.count("agent") > lookups
        return {"decision": Decision(decision="approve", policy_ids=["§4.1"], reason="ok")
                if done else None}

    graph = wire(
        retrieve=node("retrieve"),
        agent=node("agent", agent),
        tools_node=node("tools"),
        guard=node("guard", lambda s: {"verdict": Verdict(s["decision"].decision)}),
        record=node("record", lambda s: {"decision_id": "row-1"}),
    ).compile()
    final = graph.invoke(initial_state({"employee_id": "E019"}, record=record))
    assert final["verdict"] == Verdict("approve")
    assert final["decision_id"] == ("row-1" if record else None)
    return visited


def test_a_live_review_loops_over_lookups_then_guards_and_records():
    assert run_with_stubs(record=True, lookups=2) == [
        "retrieve", "agent", "tools", "agent", "tools", "agent", "guard", "record"]


def test_an_eval_run_is_guarded_but_never_recorded():
    assert run_with_stubs(record=False) == ["retrieve", "agent", "tools", "agent", "guard"]


def test_the_mermaid_drawing_shows_every_node_and_branch():
    drawing = mermaid()
    for node in ("retrieve", "agent", "tools", "guard", "record"):
        assert f"{node}({node})" in drawing
    for edge in ("agent -. &nbsp;lookup&nbsp; .-> tools", "agent -. &nbsp;decided&nbsp; .-> guard",
                 "guard -. &nbsp;live run&nbsp; .-> record", "guard -. &nbsp;eval run&nbsp; .-> __end__",
                 "record --> __end__", "tools --> agent", "retrieve --> agent"):
        assert edge in drawing, edge

