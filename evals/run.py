"""Run the agent over the eval dataset in LangSmith and score every case.

    python -m evals.run baseline

The experiment name is the prefix plus a LangSmith suffix. Which prompt commit
and model ran is recorded in the experiment metadata, and a per-slice summary
is printed and saved under evals/results/.
"""
import sys
from collections import defaultdict
from pathlib import Path

from langsmith import evaluate

from evals import checks
from evals.dataset import DATASET
from expense_agent.agent import _graph, review_expense
from expense_agent.config import load_settings
from expense_agent.db import connect
from expense_agent.judge import SCORES, judge

RESULTS = Path(__file__).with_name("results")


def target(inputs: dict) -> dict:
    r = review_expense(inputs["request"], record=False)
    return {"decision": r.decision, "model_decision": r.model_decision, "reason": r.reason,
            "policy_ids": r.policy_ids, "retrieved_ids": r.retrieved_ids,
            "tools_called": r.tools_called, "tool_results": r.tool_results,
            "guard_overruled": r.guard_overruled, "guard_reason": r.guard_reason}


def judged(inputs: dict, outputs: dict, reference_outputs: dict) -> list[dict]:
    j = judge(inputs["request"], outputs["retrieved_ids"], outputs["tool_results"],
              outputs["decision"], outputs["reason"], reference_outputs["reference"])
    return [{"key": k, "score": getattr(j, k), "comment": j.evidence.get(k)} for k in SCORES]


def guard_overruled(outputs: dict) -> dict:
    return {"key": "guard_overruled", "score": int(outputs["guard_overruled"])}


def known_sections() -> set[str]:
    with connect() as c, c.cursor() as cur:
        cur.execute("select section_id from policy_chunks")
        return {r[0] for r in cur.fetchall()}


def summarize(results) -> str:
    """Mean of every score, overall and per slice, as a markdown table."""
    by_slice = defaultdict(lambda: defaultdict(list))
    for row in results:
        slice_ = row["example"].metadata["slice"]
        for r in row["evaluation_results"]["results"]:
            if r.score is not None:
                by_slice[slice_][r.key].append(r.score)
                by_slice["all"][r.key].append(r.score)
    keys = ["decision", "error_cost", "citation", "citations_exist", "retrieval", "tools",
            "guardrail", "guard_overruled", *SCORES]
    lines = ["| slice | n | " + " | ".join(keys) + " |", "|---|---|" + "---|" * len(keys)]
    for slice_ in ["all"] + sorted(k for k in by_slice if k != "all"):
        scores = by_slice[slice_]
        cells = [f"{sum(v) / len(v):.2f}" if (v := scores.get(k)) else "" for k in keys]
        lines.append(f"| {slice_} | {len(scores['decision'])} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main(prefix: str) -> None:
    settings = load_settings()
    _, prompt_commit = _graph()
    known = known_sections()
    results = evaluate(
        target,
        data=DATASET,
        evaluators=[checks.decision, checks.error_cost, checks.citation,
                    lambda outputs: checks.citations_exist(outputs, known),
                    checks.tools, checks.guardrail, checks.retrieval, guard_overruled, judged],
        experiment_prefix=prefix,
        metadata={"prompt": settings.agent_prompt, "prompt_commit": prompt_commit,
                  "model": settings.agent_model, "judge_model": settings.judge_model},
        max_concurrency=4,
    )
    table = summarize(results)
    header = (f"# {results.experiment_name}\n\nprompt {settings.agent_prompt} "
              f"(commit {prompt_commit}), model {settings.agent_model}, "
              f"judge {settings.judge_model}\n\n")
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / f"{results.experiment_name}.md").write_text(header + table + "\n", encoding="utf-8")
    print(header + table)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "run")
