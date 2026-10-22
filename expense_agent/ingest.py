"""Load the policy PDF into the policy_chunks table, one row per section.

Usage: python -m expense_agent.ingest

Safe to run again: sections are upserted by ID, and sections no longer in the
PDF are removed.
"""
import re
from dataclasses import dataclass

import psycopg
from openai import OpenAI
from pypdf import PdfReader

from expense_agent.config import Settings, load_settings

CHAPTER = re.compile(r"^(\d+)\. (.+)$")
SECTION = re.compile(r"^(§\d+\.\d+) (.+)$")
# Page furniture the PDF adds to every page.
FOOTER = re.compile(r"^(Northwind Labs \| Policy .* \| Confidential|Page \d+)$")
BULLETS = "•·"


@dataclass
class Chunk:
    section_id: str
    chapter: str
    title: str
    content: str


def read_lines(pdf_path) -> list[str]:
    lines: list[str] = []
    for page in PdfReader(str(pdf_path)).pages:
        for raw in (page.extract_text() or "").splitlines():
            line = raw.strip().lstrip(BULLETS).strip()
            if line and not FOOTER.match(line):
                lines.append(line)
    return lines


def policy_version(lines: list[str]) -> str:
    for line in lines:
        m = re.match(r"^Version:\s*(.+)$", line)
        if m:
            return m.group(1).strip()
    raise ValueError("No 'Version:' line found in the policy PDF")


def split_sections(lines: list[str]) -> list[Chunk]:
    chunks: list[Chunk] = []
    chapter, current = "", None
    for line in lines:
        section = SECTION.match(line)
        chapter_match = CHAPTER.match(line)
        if section:
            current = Chunk(section.group(1), chapter, section.group(2), "")
            chunks.append(current)
        elif chapter_match:
            chapter = line
            current = None
        elif current is not None:
            current.content += line + "\n"
    return chunks


def embedding_input(chunk: Chunk, version: str) -> str:
    return (
        f"{chunk.section_id} {chunk.title} ({chunk.chapter}, policy v{version})\n"
        f"{chunk.content.strip()}"
    )


def embed(client: OpenAI, model: str, texts: list[str]) -> list[list[float]]:
    response = client.embeddings.create(model=model, input=texts)
    return [item.embedding for item in response.data]


def ingest(settings: Settings) -> int:
    lines = read_lines(settings.policy_pdf)
    version = policy_version(lines)
    chunks = split_sections(lines)
    if not chunks:
        raise ValueError("No sections found; check the PDF extraction")

    vectors = embed(OpenAI(), settings.embedding_model,
                    [embedding_input(c, version) for c in chunks])

    with psycopg.connect(settings.database_url) as conn, conn.cursor() as cur:
        for chunk, vec in zip(chunks, vectors):
            cur.execute(
                """
                insert into policy_chunks
                    (section_id, chapter, title, content, policy_version, embedding)
                values (%s, %s, %s, %s, %s, %s::vector)
                on conflict (section_id) do update set
                    chapter = excluded.chapter, title = excluded.title,
                    content = excluded.content, policy_version = excluded.policy_version,
                    embedding = excluded.embedding
                """,
                (chunk.section_id, chunk.chapter, chunk.title,
                 chunk.content.strip(), version, str(vec)),
            )
        cur.execute("delete from policy_chunks where section_id <> all(%s)",
                    ([c.section_id for c in chunks],))
    return len(chunks)


if __name__ == "__main__":
    count = ingest(load_settings())
    print(f"Loaded {count} policy sections.")
