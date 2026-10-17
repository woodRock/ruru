"""Citation keys: <first author surname><year><first significant title word>, e.g. wood2026joint.

The surname is the last word of the family name, so particles drop out ('van der Maaten' →
'maaten'). Clashes get a, b, …, z, aa, ab, …. Once assigned a key never changes (`ruru rekey`).
"""

from __future__ import annotations

import itertools
from collections.abc import Container, Iterator

from ruru.text import split_name, words

STOPWORDS = frozenset({
    "a", "an", "the", "on", "of", "in", "for", "to", "and", "or", "with", "without", "towards",
    "toward", "via", "from", "by", "at", "into", "onto", "is", "are", "be", "do", "does", "can",
    "how", "what", "when", "why", "which", "using", "use", "new", "its", "their", "as", "we", "our",
})


def base_key(authors: list[str], year: int | None, title: str) -> str:
    surname = "anon"
    if authors:
        family = words(split_name(authors[0])[1])
        if family:
            surname = family[-1]
    title_words = words(title)
    word = next((w for w in title_words if w not in STOPWORDS), title_words[0] if title_words else "untitled")
    return f"{surname}{year if year else 'nd'}{word}"


def _suffixes() -> Iterator[str]:
    letters = "abcdefghijklmnopqrstuvwxyz"
    for n in itertools.count(1):
        for combo in itertools.product(letters, repeat=n):
            yield "".join(combo)


def unique_key(base: str, taken: Container[str]) -> str:
    if base not in taken:
        return base
    return next(base + s for s in _suffixes() if base + s not in taken)
