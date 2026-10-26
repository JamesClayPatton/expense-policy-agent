"""The RAG step: find the policy sections that apply to an expense.

The policy is small (about 20 sections), so this is an exact cosine search over
policy_chunks with no index. Each hit is a whole section, cited by its ID.
"""
from dataclasses import dataclass

import psycopg
from openai import OpenAI

from expense_agent.config import load_settings
from expense_agent.db import connect

# Sections that apply to every expense, whatever it sounds like: eligibility,
# approval authority, splits, duplicates, budgets, prohibited items, gaps. The
# baseline missed these constantly because an expense's text rarely resembles
# them, so they are always in context and the search picks the category rules.
CONTROL_SECTIONS = ("§1.1", "§1.2", "§2.1", "§2.2", "§2.3", "§8.1", "§9.1", "§9.2")
TOP_K = 4


@dataclass
class Section:
    section_id: str
    chapter: str
    title: str
    content: str
    score: float | None = None   # cosine similarity to the query, if searched


def embed_query(text: str) -> list[float]:
    settings = load_settings()
    response = OpenAI().embeddings.create(model=settings.embedding_model, input=[text])
    return response.data[0].embedding


def query_text(request: dict) -> str:
    """What gets embedded. The free text goes in because it carries the most
    signal about what the expense actually is."""
    parts = [
        f"Expense category: {request.get('category', '')}",
        f"Vendor: {request.get('vendor', '')}",
        f"Amount: {request.get('amount', '')}",
        f"Notes: {request.get('notes', '')}",
        f"Receipt: {request.get('receipt_text', '')}",
    ]
    return "\n".join(p for p in parts if not p.endswith(": "))


def _rows_to_sections(rows) -> list[Section]:
    return [Section(r[0], r[1], r[2], r[3], float(r[4]) if len(r) > 4 and r[4] is not None else None)
            for r in rows]


def search(text: str, k: int = TOP_K, exclude: tuple[str, ...] = (),
           conn: psycopg.Connection | None = None) -> list[Section]:
    """Top-k policy sections by cosine similarity to the text."""
    vec = embed_query(text)

    def query(c):
        with c.cursor() as cur:
            cur.execute(
                """
                select section_id, chapter, title, content,
                       1 - (embedding <=> %s::vector) as score
                from policy_chunks
                where section_id != all(%s)
                order by embedding <=> %s::vector
                limit %s
                """,
                (str(vec), list(exclude), str(vec), k),
            )
            return _rows_to_sections(cur.fetchall())

    if conn is not None:
        return query(conn)
    with connect() as own:
        return query(own)


def all_sections(conn: psycopg.Connection | None = None) -> list[Section]:
    """The whole policy, in order. For the single-prompt baseline, which has no retrieval."""
    def query(c):
        with c.cursor() as cur:
            cur.execute("select section_id, chapter, title, content from policy_chunks")
            return sorted(_rows_to_sections(cur.fetchall()),
                          key=lambda s: tuple(int(n) for n in s.section_id[1:].split(".")))

    if conn is not None:
        return query(conn)
    with connect() as own:
        return query(own)


def retrieve(request: dict, conn: psycopg.Connection | None = None) -> list[Section]:
    """The sections the agent sees: the control sections, then the best matches."""
    return (get_sections(list(CONTROL_SECTIONS), conn=conn)
            + search(query_text(request), exclude=CONTROL_SECTIONS, conn=conn))


def get_sections(section_ids: list[str], conn: psycopg.Connection | None = None) -> list[Section]:
    """Fetch sections by ID. Used to check citations and to add fixed context."""
    if not section_ids:
        return []

    def query(c):
        with c.cursor() as cur:
            cur.execute(
                """
                select section_id, chapter, title, content
                from policy_chunks where section_id = any(%s)
                order by section_id
                """,
                (section_ids,),
            )
            return _rows_to_sections(cur.fetchall())

    if conn is not None:
        return query(conn)
    with connect() as own:
        return query(own)


def format_sections(sections: list[Section]) -> str:
    """The text the agent sees in its prompt."""
    return "\n\n".join(f"[{s.section_id} {s.title}]\n{s.content}" for s in sections)
