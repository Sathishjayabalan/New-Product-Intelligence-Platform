"""Lightweight NLP primitives used by the engines (tokenisation, hashing,
similarity). Pure-Python so the pipeline has no heavy ML dependencies."""

import hashlib
import re

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "is", "are", "was", "were", "be",
    "to", "of", "in", "on", "for", "with", "at", "by", "from", "as", "it",
    "this", "that", "we", "our", "they", "their", "i", "my", "me", "you",
    "your", "have", "has", "had", "not", "no", "so", "too", "very", "can",
    "will", "just", "do", "does", "did", "when", "while", "about", "after",
    "before", "more", "most", "some", "than", "then", "them", "there",
}


def tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9']+", text.lower())
    return [w for w in words if w not in STOPWORDS and len(w) > 2]


def token_set(text: str) -> set[str]:
    return set(tokenize(text))


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize(text).encode()).hexdigest()


def top_terms(texts: list[str], n: int = 5) -> list[str]:
    """Most frequent informative terms across a set of texts."""
    counts: dict[str, int] = {}
    for t in texts:
        for tok in tokenize(t):
            counts[tok] = counts.get(tok, 0) + 1
    return [w for w, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:n]]
