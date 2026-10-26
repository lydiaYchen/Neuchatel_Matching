"""Co-author conflicts: a reviewer who wrote a paper with one of a paper's authors.

The reviewer's co-authors of the last `coauthor_years` years come from their
DBLP papers (`dblp.load_coauthors`). Where the author is identified on DBLP by
the ORCID in the authors CSV and the reviewer's co-authors are known by DBLP
page, people are compared, not names, so a namesake no longer conflicts.
Otherwise names are compared (`names.names_match`), erring towards a conflict:
a lost reviewer slot is cheaper than a missed conflict. A co-author on a DBLP
catch-all page (papers DBLP has not assigned to anyone) is always compared by
name. A reviewer DBLP cannot place has no co-authors here and passes this
layer: `CoauthorIndex.uncovered` counts them.
"""

from __future__ import annotations

import datetime
from collections import defaultdict
from dataclasses import dataclass

from .. import hotcrp
from ..names import MIN_MATCH_TOKENS, names_match, token_set

# Strength of a match as evidence, for keeping one per reviewer and paper.
MATCH_RANK = {"partial": 0, "catch-all": 1, "exact": 2, "dblp": 3}


@dataclass(frozen=True)
class CoauthorConflict:
    author_name: str
    match: str            # 'dblp' | 'exact' | 'catch-all' | 'partial'
    shared: int           # co-authored papers inside the window
    latest_year: int
    latest_title: str


class CoauthorIndex:
    """Reviewers looked up by who they published with: by name (inverted by
    word, so a lookup touches a few buckets), and by DBLP page."""

    def __init__(self) -> None:
        self._by_token: dict[str, set[frozenset[str]]] = defaultdict(set)
        self._entries: dict[frozenset[str], dict[str, tuple[int, int, str]]] = defaultdict(dict)
        self._by_pid: dict[str, dict[str, tuple[int, int, str]]] = defaultdict(dict)
        self.covered: set[str] = set()      # reviewers with co-author data
        self.uncovered: set[str] = set()    # without: this layer cannot see them
        self.id_covered: set[str] = set()   # reviewers whose co-authors are known by page
        self.catch_all: CoauthorIndex | None = None   # co-authors on catch-all pages, by name

    def add(self, email: str, tokens: frozenset[str], evidence: tuple[int, int, str]) -> None:
        if len(tokens) < MIN_MATCH_TOKENS:
            return
        held = self._entries[tokens].get(email)
        if held is not None:
            # Two spellings folding to one name: add the counts, keep the newest paper.
            shared, year, title = evidence
            if held[1] > year:
                year, title = held[1], held[2]
            evidence = (held[0] + shared, year, title)
        self._entries[tokens][email] = evidence
        for token in tokens:
            self._by_token[token].add(tokens)

    def lookup(self, tokens: frozenset[str]) -> dict[str, tuple[str, tuple[int, int, str]]]:
        """{reviewer email: (match, evidence)} for everyone who wrote with this name."""
        if len(tokens) < MIN_MATCH_TOKENS:
            return {}
        found: dict[str, tuple[str, tuple[int, int, str]]] = {}
        seen: set[frozenset[str]] = set()
        for token in tokens:
            for candidate in self._by_token.get(token, ()):
                if candidate in seen:
                    continue
                seen.add(candidate)
                match = names_match(candidate, tokens)
                if match is None:
                    continue
                for email, evidence in self._entries[candidate].items():
                    if email not in found or (match == "exact" and found[email][0] == "partial"):
                        found[email] = (match, evidence)
        return found

    def lookup_pid(self, pid: str) -> dict[str, tuple[int, int, str]]:
        return self._by_pid.get(pid, {})


def build_coauthor_index(
    people, coauthors: dict[str, dict[str, list]], coauthor_ids: dict[str, dict[str, list]],
    catch_all: dict[str, str], *, years: int,
) -> CoauthorIndex:
    """Index `people` by their co-authors of the last `years` calendar years
    (`dblp.load_coauthors`/`load_coauthor_ids`)."""
    cutoff = datetime.date.today().year - years + 1
    index = CoauthorIndex()
    for person in people:
        shared = coauthors.get(person.pid) if person.pid else None
        if shared is None:
            index.uncovered.add(person.email)
            continue
        index.covered.add(person.email)
        own = token_set(person.name)
        for name, pubs in shared.items():
            windowed = [(int(y), t) for y, t in pubs if int(y) >= cutoff]
            if not windowed:
                continue
            tokens = token_set(name)
            if not tokens or tokens == own:   # not their own co-author
                continue
            latest_year, latest_title = max(windowed)
            index.add(person.email, tokens, (len(windowed), latest_year, latest_title))
    index.catch_all = CoauthorIndex()
    for person in people:
        pages = coauthor_ids.get(person.pid) if person.pid else None
        if pages is None:
            continue
        index.id_covered.add(person.email)
        for pid, pubs in pages.items():
            windowed = [(int(y), t) for y, t in pubs if int(y) >= cutoff]
            if not windowed:
                continue
            latest_year, latest_title = max(windowed)
            evidence = (len(windowed), latest_year, latest_title)
            if pid in catch_all:
                tokens = token_set(catch_all[pid])
                if tokens != token_set(person.name):
                    index.catch_all.add(person.email, tokens, evidence)
            else:
                index._by_pid[pid][person.email] = evidence
    return index


def _coauthor_matches(index: CoauthorIndex, tokens: frozenset[str], author_pid: str | None):
    """{reviewer email: (match, evidence)} for one person on a paper.

    By name unless the author has a DBLP page; then by page for every reviewer
    whose co-authors are known by page, by name for the rest, and by name
    against those reviewers' catch-all co-authors, who could be anyone.
    """
    by_name = index.lookup(tokens)
    if author_pid is None:
        return by_name
    found = {email: hit for email, hit in by_name.items() if email not in index.id_covered}
    for email, (_, evidence) in index.catch_all.lookup(tokens).items():
        found[email] = ("catch-all", evidence)
    for email, evidence in index.lookup_pid(author_pid).items():
        found[email] = ("dblp", evidence)
    return found


def coauthor_conflicts(
    papers: list[dict], index: CoauthorIndex, author_pids: dict[str, str],
) -> dict[int, dict[str, CoauthorConflict]]:
    """{pid: {reviewer email: the strongest co-authorship with one of its people}}."""
    derived: dict[int, dict[str, CoauthorConflict]] = {}
    for paper in papers:
        people: dict[tuple[frozenset[str], str], str] = {}
        for person in hotcrp.paper_people(paper):
            display = hotcrp.person_name(person)
            tokens = token_set(display)
            if tokens:
                people.setdefault((tokens, (person.get("email") or "").strip().lower()), display)
        found: dict[str, CoauthorConflict] = {}
        for (tokens, author_email), display in people.items():
            for email, (match, (shared, year, title)) in _coauthor_matches(
                    index, tokens, author_pids.get(author_email)).items():
                held = found.get(email)
                if held is not None and not (
                    MATCH_RANK[match] > MATCH_RANK[held.match]
                    or (match == held.match and shared > held.shared)
                ):
                    continue
                found[email] = CoauthorConflict(display, match, shared, year, title)
        if found:
            derived[paper["pid"]] = found
    return derived
