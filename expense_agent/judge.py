"""The LLM judge. Grades an agent decision's explanation on a different model
from the agent (Claude), using the expense-judge prompt in LangSmith.

It never decides whether the expense should be approved; it scores whether
the reason given is grounded, complete, clear and safe.
"""
import json
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache

from anthropic import Anthropic
from langsmith import Client, traceable
from langsmith.wrappers import wrap_anthropic
from psycopg.types.json import Jsonb

from expense_agent import retrieval
from expense_agent.config import load_settings
from expense_agent.db import connect
from expense_agent.graph import format_request

SCORES = ("grounded", "complete", "clear", "safe")
NO_REFERENCE = ("No reference for this review. Judge against the policy sections "
                "and the tool results only.")

# The judge's reply is held to this schema by the API, so every score is
# present and the JSON always parses.
SCHEMA = {
    "type": "object",
    "properties": {
        **{k: {"type": "integer", "enum": [1, 2, 3, 4, 5]} for k in SCORES},
        "evidence": {"type": "object", "properties": {k: {"type": "string"} for k in SCORES},
                     "required": list(SCORES), "additionalProperties": False},
    },
    "required": [*SCORES, "evidence"],
    "additionalProperties": False,
}


@dataclass
class Judgement:
    grounded: int
    complete: int
    clear: int
    safe: int
    evidence: dict[str, str] = field(default_factory=dict)
    judge_model: str = ""
    judge_prompt: str = ""

    @property
    def scores(self) -> dict[str, int]:
        return {k: getattr(self, k) for k in SCORES}


def _parse(text: str) -> dict:
    """The judge is asked for JSON only, but strip a code fence if one shows up."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    return json.loads(text)


def _text(content) -> str:
    """The reply's text block. The model may put a thinking block before it."""
    return next(b.text for b in content if b.type == "text")


@traceable(name="judge_decision", run_type="chain")
def judge(request: dict, retrieved_ids: list[str], tool_results: list[dict],
          decision: str, reason: str, reference: str = NO_REFERENCE) -> Judgement:
    settings = load_settings()
    prompt = Client().pull_prompt(settings.judge_prompt)
    system, human = prompt.format_messages(
        request=format_request(request),
        policy_sections=retrieval.format_sections(retrieval.get_sections(retrieved_ids)),
        tool_results=json.dumps(tool_results, indent=1, default=str),
        decision=decision,
        reason=reason,
        reference=reference,
    )
    reply = wrap_anthropic(Anthropic()).messages.create(
        model=settings.judge_model, max_tokens=1200,
        system=system.content,
        messages=[{"role": "user", "content": human.content}],
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
    )
    data = _parse(_text(reply.content))
    return Judgement(
        **{k: int(data[k]) for k in SCORES},
        evidence={k: str(v) for k, v in data.get("evidence", {}).items()},
        judge_model=settings.judge_model,
        judge_prompt=settings.judge_prompt,
    )


def record_judgement(decision_id: str, j: Judgement) -> str:
    with connect() as c, c.cursor() as cur:
        cur.execute(
            """
            insert into judgements (decision_id, grounded, complete, clear, safe,
                                    evidence, judge_model, judge_prompt)
            values (%s, %s, %s, %s, %s, %s, %s, %s) returning judgement_id
            """,
            (decision_id, j.grounded, j.complete, j.clear, j.safe,
             Jsonb(j.evidence), j.judge_model, j.judge_prompt),
        )
        return str(cur.fetchone()[0])


@lru_cache(maxsize=1)
def _project_id() -> str:
    return str(Client().read_project(project_name=os.environ["LANGSMITH_PROJECT"]).id)


def post_feedback(run_id: str, j: Judgement) -> None:
    """Attach the scores to the agent's LangSmith trace, one feedback per score."""
    client = Client()
    for key in SCORES:
        client.create_feedback(run_id, key=key, score=getattr(j, key),
                               comment=j.evidence.get(key), source_info={"judge": j.judge_model},
                               session_id=_project_id())


def judge_and_record(decision_id: str, run_id: str | None, request: dict,
                     retrieved_ids: list[str], tool_results: list[dict],
                     decision: str, reason: str) -> Judgement:
    """Judge a recorded decision, save the scores, and post them to LangSmith."""
    j = judge(request, retrieved_ids, tool_results, decision, reason)
    record_judgement(decision_id, j)
    if run_id:
        post_feedback(run_id, j)
    return j
