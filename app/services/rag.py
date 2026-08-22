"""
Curriculum retrieval.

Grounds every answer Souly gives in a page of a real Ministry book. Only books
a human marked verified are searchable — the agent is not allowed to teach
from material nobody checked.

WHAT CHANGED IN schema_v5
-------------------------
This used to index `lesson_steps.body`: prose written into SQLite by a seed
script. The books are real now, so the corpus is the OCR text of the mapped
pages, held on disk beside the PDFs and read through `services.curriculum`.
`curriculum_pages` itself holds no text at all — only which lesson is on which
page — so there is no second copy of the book to drift out of date.

Implementation is deliberately dependency-free: TF-IDF-ish scoring over the
page text. For a unit of a few dozen pages this is both fast and completely
predictable, and it works on a laptop with no internet — which is the
situation you will be in at least once during this project.

ChromaDB + sentence-transformers remain the Phase 2+ upgrade path. Swap the
body of `search()`; the signature and the shape of `Chunk` are the contract
the rest of the system depends on.
"""

import math
import re
import sqlite3
from dataclasses import dataclass

from app.services import curriculum

# Words too common to carry meaning in a retrieval score.
STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "of", "to", "in", "on", "at", "for", "with", "by", "from", "as", "into",
    "and", "or", "but", "if", "then", "than", "so", "because",
    "i", "you", "he", "she", "it", "we", "they", "me", "him", "her", "them",
    "my", "your", "his", "its", "our", "their", "this", "that", "these", "those",
    "what", "which", "who", "whom", "when", "where", "why", "how",
    "do", "does", "did", "can", "could", "will", "would", "should", "may",
    "have", "has", "had", "not", "no", "yes", "s", "t",
    "tell", "explain", "about", "please", "help", "know", "want", "like",
}


@dataclass
class Chunk:
    """One retrieved page, with enough provenance to cite it."""

    text: str
    score: float
    page_id: int
    page: int
    lesson: str
    book_id: int
    book_title: str
    subject_name: str
    topic_id: int | None
    topic_title: str | None

    def to_ref(self) -> dict:
        return {
            "page_id": self.page_id,
            "page": self.page,
            "lesson": self.lesson,
            "book": self.book_title,
            "subject": self.subject_name,
            "topic_id": self.topic_id,
            "topic": self.topic_title,
            "score": round(self.score, 3),
        }


def tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return [w for w in words if w not in STOPWORDS and len(w) > 1]


def _stem(word: str) -> str:
    """
    Crude singularisation so "fractions" matches "fraction".

    A real stemmer would be better, but this covers the plural/singular case
    that actually breaks child-phrased questions ("tell me about fractions")
    without adding a dependency.
    """
    if len(word) > 4:
        if word.endswith("ies"):
            return word[:-3] + "y"
        if word.endswith("es") and not word.endswith("ses"):
            return word[:-2]
        if word.endswith("s") and not word.endswith("ss"):
            return word[:-1]
    return word


def _stems(text: str) -> list[str]:
    return [_stem(w) for w in tokenize(text)]


def _candidate_pages(
    conn: sqlite3.Connection,
    *,
    subject_code: str | None,
    topic_id: int | None,
    grade: str | None,
    verified_only: bool,
) -> list[sqlite3.Row]:
    sql = """
        SELECT p.id AS page_id, p.page, p.lesson,
               b.id AS book_id, b.code AS book_code, b.title AS book_title,
               b.subject AS subject_name, b.subject_code, b.grade,
               t.id AS topic_id, t.title AS topic_title
        FROM curriculum_pages p
        JOIN curriculum_books b ON b.id = p.book_id
        LEFT JOIN topics t ON t.book_id = b.id AND t.lesson_label = p.lesson
        WHERE 1=1
    """
    params: list[object] = []

    if verified_only:
        sql += " AND b.is_verified = 1"
    if topic_id is not None:
        sql += " AND t.id = ?"
        params.append(topic_id)
    if subject_code is not None:
        sql += " AND UPPER(COALESCE(b.subject_code, b.subject)) = ?"
        params.append(subject_code.upper())
    if grade is not None:
        sql += " AND b.grade = ?"
        params.append(str(grade))

    return conn.execute(sql, params).fetchall()


def search(
    conn: sqlite3.Connection,
    query: str,
    *,
    limit: int = 4,
    subject_code: str | None = None,
    topic_id: int | None = None,
    grade: str | None = None,
    verified_only: bool = True,
) -> list[Chunk]:
    """
    Return the pages most relevant to `query`.

    This is the `search_curriculum` tool from the roadmap's agent tool list.
    """
    query_stems = _stems(query)
    if not query_stems:
        return []

    rows = _candidate_pages(
        conn, subject_code=subject_code, topic_id=topic_id,
        grade=grade, verified_only=verified_only,
    )
    if not rows:
        return []

    # Document frequency across the candidate set, so a word appearing on
    # every page ("lesson", "number") counts for less than a rare one.
    docs = []
    df: dict[str, int] = {}
    for row in rows:
        body = curriculum.page_text(row["book_code"], row["page"])
        if not body:
            # Not ingested, or OCR produced nothing. Retrieval must not invent
            # a page it cannot read.
            continue
        stems = _stems(f"{row['lesson']} {row['topic_title'] or ''} {body}")
        docs.append((row, body, stems, set(stems)))
        for stem in set(stems):
            df[stem] = df.get(stem, 0) + 1

    if not docs:
        return []

    n_docs = len(docs)
    scored: list[Chunk] = []

    for row, body, stems, stem_set in docs:
        score = 0.0
        for q in query_stems:
            if q not in stem_set:
                continue
            tf = stems.count(q) / len(stems)
            idf = math.log((n_docs + 1) / (df.get(q, 0) + 1)) + 1.0
            score += tf * idf

        # Lesson-title matches are a strong signal for a child's question,
        # which is usually a topic name ("help with fractions") rather than a
        # sentence.
        title_stems = set(_stems(f"{row['lesson']} {row['topic_title'] or ''}"))
        score += len(set(query_stems) & title_stems) * 0.35

        if score <= 0:
            continue

        scored.append(Chunk(
            text=body,
            score=score,
            page_id=row["page_id"],
            page=row["page"],
            lesson=row["lesson"],
            book_id=row["book_id"],
            book_title=row["book_title"],
            subject_name=row["subject_name"] or "",
            topic_id=row["topic_id"],
            topic_title=row["topic_title"],
        ))

    scored.sort(key=lambda c: c.score, reverse=True)
    return scored[:limit]


def build_context(chunks: list[Chunk], max_chars: int = 2400) -> str:
    """
    Format retrieved pages for the LLM prompt, most relevant first.

    Every block carries its page number. That is not decoration: it is how a
    claim in Souly's answer gets traced back to the Ministry book it came
    from.
    """
    parts, total = [], 0
    for i, chunk in enumerate(chunks, 1):
        head = (f"[{i}] {chunk.subject_name} > {chunk.lesson} "
                f"({chunk.book_title}, page {chunk.page})")
        block = f"{head}\n{chunk.text}"
        if total + len(block) > max_chars:
            # Take what fits of the first block rather than returning nothing.
            if not parts:
                parts.append(block[:max_chars])
            break
        parts.append(block)
        total += len(block)
    return "\n\n".join(parts)


def coverage(conn: sqlite3.Connection) -> dict:
    """
    How much verified curriculum actually exists.

    Surfaced on /health because "the RAG returns nothing" and "the RAG is
    broken" look identical from the UI, and the first one is usually the real
    answer.
    """
    return curriculum.coverage(conn)
