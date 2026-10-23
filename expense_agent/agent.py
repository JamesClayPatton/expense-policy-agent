"""The expense review agent. The web page calls review_expense() and nothing else."""
from dataclasses import dataclass, field


@dataclass
class Review:
    decision: str                       # approve | flag | reject
    reason: str
    policy_ids: list[str] = field(default_factory=list)
    tools_called: list[str] = field(default_factory=list)
    gate_overruled: bool = False
    trace_url: str | None = None


def review_expense(request: dict) -> Review:
    raise NotImplementedError("The review agent has not been built yet.")
