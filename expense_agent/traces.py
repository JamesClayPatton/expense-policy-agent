"""Links to LangSmith traces."""
import logging

from langsmith import Client
from langsmith.utils import LangSmithError

log = logging.getLogger("expense_agent")


def public_trace_url(run_id: str) -> str | None:
    """A share link for the trace that needs no LangSmith account. None when
    LangSmith does not have the run, for example once the account's trace limit
    is hit, so the caller can fall back to the private link."""
    client = Client()
    try:
        return client.read_run_shared_link(run_id) or client.share_run(run_id)
    except LangSmithError as e:
        log.warning("could not share trace %s: %s", run_id, e)
        return None
