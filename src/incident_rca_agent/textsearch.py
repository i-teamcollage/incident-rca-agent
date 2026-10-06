"""Small BM25 keyword search. Deterministic: equal scores are ordered by id."""

from __future__ import annotations

import math
import re
from collections import Counter

STOPWORDS = frozenset(
    """a an and are as at be but by can could did do does for from had has have how i if in into is it
    its me my no not of on or our so that the their them then there these they this to us was we were what
    when where which who why will with would you your""".split()
)


def tokenize(text: str) -> list[str]:
    return [token for token in re.findall(r"[^\W_]+", text.lower()) if token not in STOPWORDS]


class BM25:
    def __init__(self, documents: dict[str, str], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.terms = {doc_id: Counter(tokenize(text)) for doc_id, text in documents.items()}
        self.lengths = {doc_id: sum(counts.values()) for doc_id, counts in self.terms.items()}
        self.average_length = (sum(self.lengths.values()) / len(self.lengths)) if self.lengths else 0.0
        document_frequency: Counter[str] = Counter()
        for counts in self.terms.values():
            document_frequency.update(counts.keys())
        total = len(self.terms)
        self.idf = {
            term: math.log(1 + (total - frequency + 0.5) / (frequency + 0.5))
            for term, frequency in document_frequency.items()
        }

    def search(self, query: str, k: int = 5) -> list[tuple[str, float]]:
        query_terms = tokenize(query)
        scores = []
        for doc_id, counts in self.terms.items():
            score = 0.0
            for term in query_terms:
                frequency = counts.get(term, 0)
                if frequency:
                    norm = 1 - self.b + self.b * self.lengths[doc_id] / self.average_length
                    score += self.idf[term] * frequency * (self.k1 + 1) / (frequency + self.k1 * norm)
            if score > 0:
                scores.append((doc_id, round(score, 6)))
        scores.sort(key=lambda item: (-item[1], item[0]))
        return scores[:k]
