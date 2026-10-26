"""The experiment report, built from hand-made results. No LLM, no LangSmith."""
from types import SimpleNamespace

from evals.run import cases, confusion, summarize


def row(case_id, slice_, expected, got, **scores):
    scores = {"decision": int(expected == got), "error_cost": 0.0, "citation": 1, **scores}
    return {
        "example": SimpleNamespace(metadata={"id": case_id, "slice": slice_},
                                   outputs={"expected": {"decision": expected}}),
        "run": SimpleNamespace(outputs={"decision": got}),
        "evaluation_results": {"results": [SimpleNamespace(key=k, score=v) for k, v in scores.items()]},
    }


RESULTS = [
    row("normal-lunch", "normal", "approve", "approve", grounded=5),
    row("normal-hotel", "normal", "approve", "flag", error_cost=0.25, grounded=4),
    row("gaming-duplicate", "gaming", "flag", "flag", grounded=3),
    row("gaming-gift-cards", "gaming", "reject", "approve", error_cost=1.0, citation=0),
]


def test_confusion_matrix_counts_expected_against_got():
    assert confusion(RESULTS) == "\n".join([
        "| expected \\ got | approve | flag | reject |",
        "|---|:---:|:---:|:---:|",
        "| approve | **1** | 1 | 0 |",
        "| flag | 0 | **1** | 0 |",
        "| reject | 1 | 0 | **0** |",
    ])


def test_cases_lists_every_case_with_its_scores():
    text = cases(RESULTS)
    assert text.splitlines()[0] == ("| case | slice | expected | got | cost | cited | "
                                    "grounded | complete | clear | safe |")
    assert "| gaming-gift-cards | gaming | reject | **approve** | 1.00 | no | – | – | – | – |" in text
    assert "| normal-lunch | normal | approve | approve | 0.00 | yes | 5 | – | – | – |" in text


def test_summarize_averages_per_slice():
    table = summarize(RESULTS)
    assert "| all | 4 | 0.50 | 0.31 | 0.75 |" in table
    assert "| gaming | 2 | 0.50 | 0.50 | 0.50 |" in table
