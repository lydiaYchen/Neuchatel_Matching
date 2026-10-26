"""Comparing people's names across HotCRP and DBLP.

Every name comparison in the pipeline goes through `token_set`: accent-folded,
lowercased words of two or more letters, DBLP's homonym number dropped ("Wei
Zhang 0012" -> {wei, zhang}) and common English short forms expanded (Rich ->
Richard). A set, so "Zhang Wei" and "Wei Zhang" compare equal.
"""

from __future__ import annotations

import re
import unicodedata

# Below this many shared name words a match is a coincidence, not a person.
MIN_MATCH_TOKENS = 2

# A given name this long or longer may stand for one it begins ("Sam" for
# "Samuel"); shorter ones ("Li", "Wu") are too often a whole different name.
MIN_SHORT_FORM = 3

# DBLP's homonym number: "Wei Zhang 0012".
_HOMONYM_SUFFIX_RE = re.compile(r"\s+\d+$")

# Deliberately small: a wrong entry here silently merges two people.
NICKNAMES = {
    "rich": "richard", "rick": "richard", "dick": "richard",
    "bill": "william", "will": "william", "billy": "william",
    "bob": "robert", "rob": "robert", "bobby": "robert",
    "mike": "michael", "mick": "michael",
    "dave": "david", "tom": "thomas", "tommy": "thomas",
    "steve": "stephen", "chris": "christopher", "jim": "james",
    "joe": "joseph", "dan": "daniel", "danny": "daniel",
    "matt": "matthew", "nick": "nicholas", "alex": "alexander",
    "andy": "andrew", "drew": "andrew", "ben": "benjamin",
    "tony": "anthony", "ted": "theodore", "sam": "samuel",
    "ed": "edward", "eddie": "edward", "jeff": "jeffrey",
    "greg": "gregory", "ken": "kenneth", "ron": "ronald",
    "pete": "peter", "phil": "philip", "vince": "vincent",
    "zach": "zachary", "josh": "joshua", "nate": "nathaniel",
    "kate": "katherine", "cathy": "catherine", "liz": "elizabeth",
    "beth": "elizabeth", "sue": "susan", "jen": "jennifer",
    "maggie": "margaret", "peggy": "margaret", "abby": "abigail",
}


def fold(text: str) -> str:
    """Accent-folded, lowercased, punctuation-collapsed form of a free-text field."""
    folded = unicodedata.normalize("NFKD", text)
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", folded.lower()).split())


def name_tokens(name: str) -> frozenset[str]:
    """The words of a name, folded, without DBLP's homonym number or initials."""
    name = _HOMONYM_SUFFIX_RE.sub("", name.strip())
    folded = unicodedata.normalize("NFKD", name)
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    tokens = re.findall(r"\w+", folded.lower())
    return frozenset(t for t in tokens if len(t) > 1)


def token_set(text: str) -> frozenset[str]:
    """`name_tokens` with short forms expanded, so Rich meets Richard."""
    return frozenset(NICKNAMES.get(t, t) for t in name_tokens(text))


def names_match(one: frozenset[str], other: frozenset[str]) -> str | None:
    """'exact', 'partial', or None for two `token_set`s.

    'partial' is a strict subset sharing at least MIN_MATCH_TOKENS words: one
    name written with a middle name the other leaves out ("David Albert Wood" /
    "David Wood"). Errs towards matching, which errs towards a conflict.
    """
    if len(one) < MIN_MATCH_TOKENS or len(other) < MIN_MATCH_TOKENS:
        return None
    if one == other:
        return "exact"
    if (one < other or other < one) and len(one & other) >= MIN_MATCH_TOKENS:
        return "partial"
    return None


def names_agree(dblp_name: str, hotcrp_name: str) -> bool:
    """Whether a DBLP person can be the person HotCRP names (a guard, not a match).

    Two shared words, or one plus a pair where one name is a short form of the
    other ("Eva"/"Evangelia", "Sam"/"Samuel"). Only asked about a DBLP person an
    ORCID already points at, to catch a publisher attaching it to the wrong
    author, never to find anyone.
    """
    a, b = name_tokens(dblp_name), name_tokens(hotcrp_name)
    shared = a & b
    if len(shared) >= MIN_MATCH_TOKENS:
        return True
    return bool(shared) and any(
        len(short) >= MIN_SHORT_FORM and long.startswith(short)
        for x, y in ((a - shared, b - shared), (b - shared, a - shared))
        for short in x for long in y
    )
