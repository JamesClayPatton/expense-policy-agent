"""Run an implementation over the eval dataset in LangSmith and score every case.

    python -m evals.run <prefix> [agent|rules|prompt]

agent is the real thing; rules and prompt are the simpler implementations in
evals/baselines.py that it is compared against. The experiment name is the
prefix plus a LangSmith suffix. The report written to evals/results/ holds the
exact configuration, the per-slice means, the confusion matrix and every case,
so a finished run can be read and reproduced without LangSmith.
"""
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

from langsmith import evaluate

from evals import baselines, checks
from evals.dataset import DATASET
from expense_agent.agent import _graph, review_expense
from expense_agent.config import AS_OF, ROOT, load_settings
from expense_agent.db import connect
from expense_agent.judge import SCORES, judge

RESULTS = Path(__file__).with_name("results")
DECISIONS = ("approve", "flag", "reject")
CONCURRENCY = 4


def target(inputs: dict) -> dict:
    r = review_expense(inputs["request"], record=False)
    return {"decision": r.decision, "model_decision": r.model_decision, "reason": r.reason,
            "policy_ids": r.policy_ids, "retrieved_ids": r.retrieved_ids,
            "tools_called": r.tools_called, "tool_results": r.tool_results,
            "guard_overruled": r.guard_overruled, "guard_reason": r.guard_reason}


TARGETS = {"agent": target, "rules": baselines.rules_only, "prompt": baselines.single_prompt}


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


def code_commit() -> str:
    """The repo commit the run used, with -dirty if the tree had changes."""
    run = lambda *a: subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    return run("rev-parse", "--short", "HEAD") + ("-dirty" if run("status", "--porcelain") else "")


# --- The report ---------------------------------------------------------------

def _scores(row) -> dict:
    return {r.key: r.score for r in row["evaluation_results"]["results"] if r.score is not None}


def summarize(results) -> str:
    """Mean of every score, overall and per slice."""
    by_slice = defaultdict(lambda: defaultdict(list))
    for row in results:
        slice_ = row["example"].metadata["slice"]
        for key, score in _scores(row).items():
            by_slice[slice_][key].append(score)
            by_slice["all"][key].append(score)
    keys = ["decision", "error_cost", "citation", "citations_exist", "retrieval", "tools",
            "guardrail", "guard_overruled", *SCORES]
    lines = ["| slice | n | " + " | ".join(keys) + " |", "|---|:---:|" + ":---:|" * len(keys)]
    for slice_ in ["all"] + sorted(k for k in by_slice if k != "all"):
        scores = by_slice[slice_]
        cells = [f"{sum(v) / len(v):.2f}" if (v := scores.get(k)) else "" for k in keys]
        lines.append(f"| {slice_} | {len(scores['decision'])} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def confusion(results) -> str:
    """Expected decision (rows) against what the implementation said (columns)."""
    counts = Counter((row["example"].outputs["expected"]["decision"], row["run"].outputs["decision"])
                     for row in results)
    lines = ["| expected \\ got | " + " | ".join(DECISIONS) + " |", "|---|" + ":---:|" * len(DECISIONS)]
    for expected in DECISIONS:
        cells = [f"**{counts[expected, got]}**" if got == expected else str(counts[expected, got])
                 for got in DECISIONS]
        lines.append(f"| {expected} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def cases(results) -> str:
    """One line per case, wrong decisions in bold."""
    lines = ["| case | slice | expected | got | cost | cited | " + " | ".join(SCORES) + " |",
             "|---|---|---|---|:---:|:---:|" + ":---:|" * len(SCORES)]
    for row in sorted(results, key=lambda r: r["example"].metadata["id"]):
        meta, expected = row["example"].metadata, row["example"].outputs["expected"]["decision"]
        got, s = row["run"].outputs["decision"], _scores(row)
        shown = got if got == expected else f"**{got}**"
        judge_cells = [str(int(s[k])) if k in s else "–" for k in SCORES]
        lines.append(f"| {meta['id']} | {meta['slice']} | {expected} | {shown} | "
                     f"{s.get('error_cost', 0):.2f} | {'yes' if s.get('citation') else 'no'} | "
                     + " | ".join(judge_cells) + " |")
    return "\n".join(lines)


def main(prefix: str, which: str = "agent") -> None:
    settings = load_settings()
    prompt_commit = _graph()[1] if which == "agent" else ""
    known = known_sections()
    config = {
        "target": which, "model": settings.agent_model if which != "rules" else "none",
        "prompt": settings.agent_prompt if which == "agent" else baselines.PROMPT_NAMES[which],
        "prompt_commit": prompt_commit, "judge_model": settings.judge_model,
        "judge_prompt": settings.judge_prompt, "embedding_model": settings.embedding_model,
        "code_commit": code_commit(), "dataset": DATASET, "as_of": AS_OF.isoformat(),
        "max_concurrency": CONCURRENCY,
    }
    results = evaluate(
        TARGETS[which],
        data=DATASET,
        evaluators=[checks.decision, checks.error_cost, checks.citation,
                    lambda outputs: checks.citations_exist(outputs, known),
                    checks.tools, checks.guardrail, checks.retrieval, guard_overruled, judged],
        experiment_prefix=prefix,
        metadata=config,
        max_concurrency=CONCURRENCY,
    )
    rows = list(results)
    report = "\n\n".join([
        f"# {results.experiment_name}",
        "## Configuration\n\n" + "\n".join(f"- {k}: `{v}`" for k, v in config.items() if v != "")
        + f"\n- cases: `{len(rows)}`",
        "## Scores by slice\n\n" + summarize(rows),
        "## Confusion matrix\n\n" + confusion(rows),
        "## Every case\n\n" + cases(rows),
    ]) + "\n"
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / f"{results.experiment_name}.md").write_text(report, encoding="utf-8")
    print(report.split("## Confusion matrix")[0])


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "run", sys.argv[2] if len(sys.argv) > 2 else "agent")
