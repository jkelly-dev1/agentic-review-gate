"""Tiny local retriever over the standards corpus.

Deliberately dependency-free: a TF cosine scorer over the in-repo corpus, NOT a
vector DB. It returns top-k chunks with scores and source ids so the analysis
can cite provenance. The scorer is the swappable seam -- replace `score_chunks`
with an embedding search / vector store without touching the graph.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.models import RetrievedChunk

CORPUS_DIR = Path(__file__).parents[1] / "data" / "corpus"
_TOKEN = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


@dataclass(frozen=True)
class CorpusDoc:
    source_id: str
    title: str
    text: str


def _parse_doc(path: Path) -> CorpusDoc:
    text = path.read_text(encoding="utf-8")
    source_id = path.stem.upper()
    title = path.stem
    body = text
    if text.startswith("---"):
        _, header, body = text.split("---", 2)
        for line in header.strip().splitlines():
            key, _, val = line.partition(":")
            if key.strip() == "id":
                source_id = val.strip()
            elif key.strip() == "title":
                title = val.strip()
    return CorpusDoc(source_id=source_id, title=title, text=body.strip())


@lru_cache(maxsize=1)
def load_corpus(corpus_dir: str = str(CORPUS_DIR)) -> tuple[CorpusDoc, ...]:
    docs = [_parse_doc(p) for p in sorted(Path(corpus_dir).glob("*.md"))]
    if not docs:
        raise FileNotFoundError(f"no corpus docs found in {corpus_dir}")
    return tuple(docs)


def _tf_vector(tokens: list[str]) -> Counter[str]:
    return Counter(tokens)


def _cosine(q: Counter[str], d: Counter[str]) -> float:
    if not q or not d:
        return 0.0
    common = set(q) & set(d)
    num = sum(q[t] * d[t] for t in common)
    qn = math.sqrt(sum(v * v for v in q.values()))
    dn = math.sqrt(sum(v * v for v in d.values()))
    if qn == 0 or dn == 0:
        return 0.0
    return num / (qn * dn)


def score_chunks(query: str, k: int = 3, corpus_dir: str | None = None) -> list[RetrievedChunk]:
    """Return the top-k corpus docs most relevant to `query`, with scores.

    One doc == one chunk here (the corpus is tiny). Only positive-scoring docs
    are returned, so an irrelevant query yields fewer than k results.
    """
    docs = load_corpus(corpus_dir) if corpus_dir else load_corpus()
    qv = _tf_vector(_tokenize(query))
    scored = [
        RetrievedChunk(source_id=d.source_id, score=round(_cosine(qv, _tf_vector(_tokenize(d.text))), 6), text=d.text)
        for d in docs
    ]
    scored = [c for c in scored if c.score > 0.0]
    scored.sort(key=lambda c: (c.score, c.source_id), reverse=True)
    return scored[:k]


def format_context(chunks: list[RetrievedChunk]) -> str:
    """Render retrieved chunks with [source:ID] provenance markers for the LLM."""
    return "\n\n".join(f"[source:{c.source_id}] (score={c.score})\n{c.text}" for c in chunks)
