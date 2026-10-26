"""Settings, read once from the environment (.env locally)."""
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# The agent's notion of "today". Fixed so results do not drift as real time
# passes: the seed data and the 30-day and monthly-budget rules depend on it.
AS_OF = date(2026, 10, 22)


@dataclass(frozen=True)
class Settings:
    database_url: str
    agent_model: str
    judge_model: str
    embedding_model: str
    agent_prompt: str      # LangSmith prompt reference, e.g. expense-agent-system:3837744f
    judge_prompt: str
    policy_pdf: Path
    public_traces: bool    # share each review's trace so the link needs no account


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not set. Copy .env.example to .env and fill it in.")
    return value


def load_settings() -> Settings:
    return Settings(
        database_url=_require("DATABASE_URL"),
        agent_model=_require("AGENT_MODEL"),
        judge_model=_require("JUDGE_MODEL"),
        embedding_model=os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small"),
        agent_prompt=_require("AGENT_PROMPT"),
        judge_prompt=_require("JUDGE_PROMPT"),
        policy_pdf=ROOT / "data" / "policy" / "northwind_expense_policy.pdf",
        public_traces=os.environ.get("PUBLIC_TRACES", "").strip().lower() in ("1", "true"),
    )
