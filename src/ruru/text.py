"""Text normalisation: accents, titles, personal names."""

from __future__ import annotations

import re
import unicodedata

# Letters NFKD does not decompose into a base letter plus combining marks.
_SPECIAL = str.maketrans({
    "ø": "o", "Ø": "O", "ß": "ss", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE",
    "ł": "l", "Ł": "L", "đ": "d", "Đ": "D", "ı": "i", "þ": "th", "Þ": "Th", "ð": "d", "Ð": "D",
})

# Lowercase name particles that belong to the family name ("Laurens van der Maaten").
PARTICLES = frozenset({
    "van", "von", "de", "der", "den", "del", "della", "di", "da", "du", "le", "la", "ter", "ten",
    "dos", "das", "do", "zu", "af", "av",
})

_WORD = re.compile(r"[a-z0-9]+")


def unaccent(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.translate(_SPECIAL))
    return "".join(c for c in s if not unicodedata.combining(c))


def words(s: str) -> list[str]:
    """Lowercase ASCII words: 'Joint Retention-Time GC–MS' → ['joint', 'retention', 'time', 'gc', 'ms']."""
    return _WORD.findall(unaccent(s).lower())


def norm_title(title: str) -> str:
    return " ".join(words(title))


def fold(s: str) -> str:
    """Case- and accent-insensitive form for search."""
    return unaccent(s).casefold()


def split_name(name: str) -> tuple[str, str]:
    """(given, family) from 'Given Family', 'Given van Family' or 'Family, Given'."""
    name = " ".join(name.split())
    if "," in name:
        family, given = (part.strip() for part in name.split(",", 1))
        return given, family
    parts = name.split(" ")
    if len(parts) == 1:
        return "", parts[0]
    i = len(parts) - 1
    while i > 1 and parts[i - 1] in PARTICLES:
        i -= 1
    return " ".join(parts[:i]), " ".join(parts[i:])


def display_name(name: str) -> str:
    """'Family, Given' (BibTeX order) → 'Given Family'; other forms unchanged apart from spacing."""
    given, family = split_name(name)
    return f"{given} {family}".strip()
