"""Brand score for a generated name: how easy it is to say, read and remember.

Every name starts at 100 and loses points for things that make a brand name weaker.
Each deduction comes with a short reason that the results page shows.

  Length            6-12 letters is ideal; shorter or longer names lose points.
  Pronunciation     hard consonant clusters, long vowel runs, triple letters, many syllables.
  Brand feel        stacked add-ons (keyword + two suffixes) and filler words like "world".
"""

from __future__ import annotations

import re
from functools import lru_cache

import settings

VOWELS = set("aeiouy")
# Three-consonant clusters that English speakers say without effort (street, world, works, ...).
EASY_CLUSTERS = {
    "str", "scr", "spr", "spl", "thr", "shr", "chr", "sch", "nch", "nth", "ngs", "nts",
    "nds", "rst", "rld", "rks", "rts", "rds", "rns", "mps", "nks", "sts", "cks", "ght",
    "tch", "ndr", "ntr", "mpl", "rtl", "rth", "lth", "nkl",
}


def _consonant_runs(name: str) -> list[str]:
    return re.findall(r"[^aeiouy]+", name)


def syllables(name: str) -> int:
    """Rough syllable count: groups of vowels, with a silent final 'e' dropped."""
    groups = re.findall(r"[aeiouy]+", name)
    count = len(groups)
    if name.endswith("e") and count > 1 and not name.endswith(("le", "ee")):
        count -= 1
    return max(1, count)


@lru_cache(maxsize=8)
def _endings(suffixes: tuple[str, ...], inflections: tuple[str, ...]) -> tuple[str, ...]:
    """Suffixes and inflections, longest first, so the longest ending matches first."""
    return tuple(sorted(set(suffixes) | set(inflections), key=len, reverse=True))


def addon_count(name: str, terms: list[str]) -> int:
    """How many prefixes and suffixes are attached to the keyword (0 for the bare keyword)."""
    joined = "".join(terms)
    position = name.find(joined)
    if position < 0:
        # Industry combinations such as "realhomes" pair one keyword word with one industry word.
        return 1
    count = 1 if position > 0 else 0
    rest = name[position + len(joined):]
    endings = _endings(tuple(settings.SUFFIXES), tuple(settings.INFLECTIONS))
    while rest:
        match = next((ending for ending in endings if rest.startswith(ending)), None)
        count += 1
        if match is None:
            break
        rest = rest[len(match):]
    return count


def score(name: str, terms: list[str]) -> tuple[int, list[str]]:
    """Brand score from 0 to 100 and the reasons for each deduction."""
    points = 100
    notes: list[str] = []

    length = len(name)
    if length < 5:
        points -= 10
        notes.append("very short")
    elif length > 16:
        points -= 30
        notes.append("too long")
    elif length > 14:
        points -= 20
        notes.append("long")
    elif length > 12:
        points -= 10
        notes.append("a little long")

    for run in _consonant_runs(name):
        if len(run) >= 4:
            points -= 30
            notes.append(f"hard to say ('{run}')")
        elif len(run) == 3 and run not in EASY_CLUSTERS:
            points -= 15
            notes.append(f"awkward sound ('{run}')")
    if re.search(r"[aeiou]{3,}", name):
        points -= 10
        notes.append("vowel run")
    if re.search(r"(.)\1\1", name):
        points -= 30
        notes.append("triple letter")
    extra_syllables = syllables(name) - 5
    if extra_syllables > 0:
        points -= 10 * extra_syllables
        notes.append("many syllables")

    addons = addon_count(name, terms)
    if addons == 2:
        points -= 15
        notes.append("two add-ons")
    elif addons >= 3:
        points -= 30
        notes.append("too many add-ons")
    fillers = [word for word in settings.BRAND_FILLER_WORDS if word in name and not any(word in term for term in terms)]
    if fillers:
        points -= 10
        notes.append(f"filler word '{fillers[0]}'")

    return max(0, min(100, points)), notes
