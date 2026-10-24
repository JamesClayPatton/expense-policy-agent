"""The eval dataset: the cases the agent is graded on.

One case per line in dataset.jsonl:

  id         slice-name, unique
  slice      which kind of case it is (see SLICES)
  request    the expense, exactly as the web form would submit it
  expected   decision      approve | flag | reject
             policy_ids    at least one of these must be cited
             must_not_say  text the reason must not contain (adversarial cases)
  reference  what a good explanation says; given to the judge

The agent is expected to call all three lookup tools on every case. A case
whose employee does not exist only needs get_employee.

Upload once with: python -m evals.dataset
The dataset is frozen after the baseline run. Cases written later go in a new
slice so the before/after comparison stays honest.
"""
import json
from pathlib import Path

from langsmith import Client

from expense_agent.config import AS_OF

DATASET = "expense-policy-agent"
PATH = Path(__file__).with_name("dataset.jsonl")
SLICES = ("normal", "boundary", "gap", "conflict", "gaming", "adversarial", "missing", "approval")


def load_cases(path: Path = PATH) -> list[dict]:
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for c in cases:
        c["request"].setdefault("submitted_date", AS_OF.isoformat())
    return cases


def upload(cases: list[dict]) -> str:
    client = Client()
    if client.has_dataset(dataset_name=DATASET):
        ds = client.read_dataset(dataset_name=DATASET)
        print(f"{DATASET} already exists with {ds.example_count} examples; not touching it")
        return str(ds.id)
    ds = client.create_dataset(DATASET, description="Expense reviews with expected decisions, "
                               "by slice. Frozen before the baseline run.")
    client.create_examples(
        dataset_id=ds.id,
        examples=[{"inputs": {"request": c["request"]},
                   "outputs": {"expected": c["expected"], "reference": c["reference"]},
                   "metadata": {"id": c["id"], "slice": c["slice"]}} for c in cases],
    )
    print(f"uploaded {len(cases)} cases to {DATASET}")
    return str(ds.id)


if __name__ == "__main__":
    upload(load_cases())
