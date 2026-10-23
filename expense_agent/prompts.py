"""Prompts are managed in LangSmith. The agent pulls the commit tagged `staging`
at run time and records the commit hash, so every trace and eval run can say
which prompt version it used."""
import re
from dataclasses import dataclass

from langsmith import Client

import expense_agent.config  # noqa: F401  (loads .env so the LangSmith key is set)

AGENT_PROMPT = "expense-agent-system"
JUDGE_PROMPT = "expense-judge"
DEFAULT_TAG = "staging"

VARIABLE = re.compile(r"(?<!\{)\{([a-z_]+)\}(?!\})")


@dataclass(frozen=True)
class Prompt:
    name: str
    commit: str
    system: str
    human: str

    @property
    def variables(self) -> set[str]:
        return set(VARIABLE.findall(self.system)) | set(VARIABLE.findall(self.human))

    def messages(self, **values) -> list[tuple[str, str]]:
        missing = self.variables - set(values)
        if missing:
            raise KeyError(f"{self.name}: missing prompt variables {sorted(missing)}")
        return [("system", self.system.format(**values)), ("human", self.human.format(**values))]


def load_prompt(name: str, tag: str = DEFAULT_TAG) -> Prompt:
    commit = Client().pull_prompt_commit(f"{name}:{tag}")
    templates = {}
    for message in commit.manifest["kwargs"]["messages"]:
        role = message["id"][-1].replace("MessagePromptTemplate", "").lower()
        templates[role] = message["kwargs"]["prompt"]["kwargs"]["template"]
    return Prompt(name, commit.commit_hash, templates["system"], templates["human"])
