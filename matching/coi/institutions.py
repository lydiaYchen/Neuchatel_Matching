"""Same-institution conflicts: a reviewer who works where an author does.

Two signals, either enough: the same organisation email domain
(`org_domain`), or the same whole affiliation name once normalised
(`affiliation_keys`). A reviewer's "All (Institution)" collaborator lines
count as affiliations, and so do the submission's own. Webmail matches
nothing, so a reviewer with only a webmail address and no usable affiliation
passes this layer (`InstitutionIndex.uncovered`).
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from .. import hotcrp
from ..names import fold
from .collaborators import is_institution_wide, parse_collaborator_lines

# Registry labels under a country code: `tsinghua.edu.cn`, `ed.ac.uk`.
SECOND_LEVEL_LABELS = frozenset({
    "ac", "co", "com", "edu", "ernet", "go", "gov", "ne", "net", "or", "org", "re", "res",
})
# Mail providers, not employers (`cernet` is China's research-network mail).
WEBMAIL_LABELS = frozenset({
    "126", "139", "163", "189", "acm", "aliyun", "aol", "cernet", "daum", "fastmail",
    "foxmail", "gmail", "gmx", "googlemail", "hanmail", "hey", "hotmail", "icloud",
    "ieee", "kakao", "live", "mac", "mail", "me", "msn", "naver", "outlook", "pm",
    "proton", "protonmail", "qq", "sina", "sohu", "tom", "tutanota", "web", "yahoo",
    "yandex", "yeah", "ymail", "zoho",
})
# Alumni mail says where someone studied, not where they work.
ALUMNI_LABELS = frozenset({"alum", "alumni"})
ALUMNI_DOMAINS = frozenset({"pku.org.cn", "tsinghua.org.cn"})
# Dropped before comparing affiliations: articles, prepositions and company
# suffixes. "University", "technology", "state" and "national" are kept: they
# tell Nanjing University from Nanjing University of Science and Technology.
AFFILIATION_FILLER = frozenset({
    "a", "at", "da", "de", "del", "della", "der", "des", "di", "du", "el", "for", "in",
    "la", "le", "of", "on", "the", "and",
    "ag", "co", "company", "corp", "corporation", "gmbh", "group", "inc", "lab", "labs",
    "laboratories", "laboratory", "limited", "llc", "ltd", "research", "technologies",
})
_ABBREVIATIONS = {"univ": "university", "inst": "institute"}
# A key made only of these names nobody in particular.
GENERIC_AFFILIATION_WORDS = frozenset({
    "candidate", "center", "centre", "college", "computer", "computing", "consultant",
    "department", "employed", "engineering", "faculty", "freelance", "independent",
    "individual", "institute", "na", "national", "none", "personal", "phd", "private",
    "professor", "researcher", "retired", "school", "science", "sciences", "self",
    "state", "student", "technology", "unaffiliated", "university", "unknown",
})
# Between two affiliations in one cell; not "&"/"and" (inside names) or ","
# ("University of California, San Diego").
_MULTI_AFFILIATION_RE = re.compile(r"[;/\n]")


def org_domain(email: str) -> str:
    """`mails.tsinghua.edu.cn` -> `tsinghua.edu.cn`, `cs.cmu.edu` -> `cmu.edu`;
    "" for webmail, alumni addresses and non-addresses."""
    email = (email or "").strip().lower()
    if "@" not in email:
        return ""
    labels = [label for label in email.rpartition("@")[2].split(".") if label]
    if len(labels) < 2:
        return ""
    width = 3 if (len(labels) >= 3 and len(labels[-1]) == 2
                  and labels[-2] in SECOND_LEVEL_LABELS) else 2
    org = labels[-width:]
    domain = ".".join(org)
    if (org[0] in WEBMAIL_LABELS or domain in ALUMNI_DOMAINS
            or ALUMNI_LABELS & set(labels[:-width])):
        return ""
    return domain


def affiliation_keys(text: str) -> set[tuple[str, ...]]:
    """One key per affiliation in a cell: its folded words in order, minus
    filler. Two affiliations are one institution when their keys are equal;
    order separates the University of Washington from Washington University."""
    keys: set[tuple[str, ...]] = set()
    for part in _MULTI_AFFILIATION_RE.split(text or ""):
        words = tuple(_ABBREVIATIONS.get(w, w) for w in fold(part).split()
                      if w not in AFFILIATION_FILLER)
        if any(len(w) >= 3 and w not in GENERIC_AFFILIATION_WORDS for w in words):
            keys.add(words)
    return keys


@dataclass(frozen=True)
class InstitutionConflict:
    kind: str            # email_domain | affiliation | declared_institution
    author_name: str
    author_affiliation: str
    evidence: str        # the shared domain, or the reviewer's matching affiliation


@dataclass
class InstitutionIndex:
    by_domain: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    by_affiliation: dict[tuple[str, ...], dict[str, str]] = field(
        default_factory=lambda: defaultdict(dict))
    uncovered: set[str] = field(default_factory=set)   # no institutional address or affiliation


def build_institution_index(reviewers, accounts: hotcrp.Accounts) -> InstitutionIndex:
    """Reviewers by organisation domain and affiliation key, their account's
    "All (Institution)" collaborator lines counting as affiliations."""
    index = InstitutionIndex()
    for r in reviewers:
        domains = {org_domain(r.email), org_domain(r.hotcrp_email)} - {""}
        affiliations = [r.affiliation or ""]
        acct = accounts.by_email.get(r.hotcrp_email)
        if acct is not None:
            affiliations.append(acct.affiliation)
            affiliations += [inst for name, inst in parse_collaborator_lines(acct.collaborators)
                             if is_institution_wide(name) and inst]
        for domain in domains:
            index.by_domain[domain].add(r.email)
        has_key = False
        for text in affiliations:
            for key in affiliation_keys(text):
                index.by_affiliation[key].setdefault(r.email, text.strip())
                has_key = True
        if not (domains or has_key):
            index.uncovered.add(r.email)
    return index


def institution_conflicts(
    papers: list[dict], index: InstitutionIndex,
) -> dict[int, dict[str, InstitutionConflict]]:
    """{pid: {reviewer email: InstitutionConflict}}; an email-domain match is
    kept over an affiliation match, a declared institution is the weakest."""
    derived: dict[int, dict[str, InstitutionConflict]] = {}
    for paper in papers:
        found: dict[str, InstitutionConflict] = {}
        for person in hotcrp.paper_people(paper):
            author_name = hotcrp.person_name(person)
            author_affiliation = (person.get("affiliation") or "").strip()
            hits: list[tuple[str, str, str]] = []
            domain = org_domain((person.get("email") or "").strip().lower())
            if domain:
                hits += [(e, "email_domain", domain) for e in index.by_domain.get(domain, ())]
            for key in affiliation_keys(author_affiliation):
                hits += [(e, "affiliation", text)
                         for e, text in index.by_affiliation.get(key, {}).items()]
            for email, kind, evidence in hits:
                held = found.get(email)
                if held is not None and (held.kind == "email_domain" or kind == "affiliation"):
                    continue
                found[email] = InstitutionConflict(kind, author_name, author_affiliation, evidence)
        for name, institution in parse_collaborator_lines(str(paper.get("collaborators") or "")):
            if not is_institution_wide(name):
                continue
            for key in affiliation_keys(institution):
                for email, text in index.by_affiliation.get(key, {}).items():
                    found.setdefault(email, InstitutionConflict(
                        "declared_institution", "", institution, text))
        if found:
            derived[paper["pid"]] = found
    return derived
