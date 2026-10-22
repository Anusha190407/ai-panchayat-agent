"""Lightweight text similarity used to shortlist possible duplicate complaints.

Works for English, Tamil script and Tanglish without any model download:
a blend of word-level cosine similarity and character-trigram overlap.
"""
from __future__ import annotations

import math
import re
from collections import Counter

_STOP = {
    "the", "a", "an", "is", "are", "was", "were", "in", "on", "at", "of", "for", "to", "and", "or", "near",
    "our", "my", "we", "i", "it", "this", "that", "there", "here", "has", "have", "been", "not", "no",
    "please", "sir", "madam", "very", "since", "from", "with", "by", "be", "street", "road", "nagar",
    "ward", "days", "day", "week", "la", "ah", "romba",
}
_TOKEN_RE = re.compile(r"[a-z0-9]+|[஀-௿]+")


def normalize(text: str) -> str:
    return " ".join(_TOKEN_RE.findall((text or "").lower()))


def tokens(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall((text or "").lower()) if t not in _STOP and len(t) > 1]


def _cosine(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    dot = sum(a[k] * b[k] for k in a.keys() & b.keys())
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def _trigrams(text: str) -> set[str]:
    s = f"  {normalize(text)}  "
    return {s[i:i + 3] for i in range(len(s) - 2)} if len(s) > 3 else set()


def similarity(a: str, b: str) -> float:
    """Similarity in [0, 1]."""
    word_score = _cosine(Counter(tokens(a)), Counter(tokens(b)))
    ta, tb = _trigrams(a), _trigrams(b)
    tri_score = len(ta & tb) / len(ta | tb) if ta and tb else 0.0
    return round(0.6 * word_score + 0.4 * tri_score, 3)
