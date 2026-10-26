"""Declared-collaborator conflicts, from HotCRP's `collaborators` fields.

A reviewer's collaborators name one of the paper's authors, an author's
(if they hold an account) name the reviewer, or the submission's own
declared collaborators name the reviewer. Names only; the "All
(Institution)" lines are the institution layer's.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .. import hotcrp
from ..names import MIN_MATCH_TOKENS, names_match, token_set

_TRAILING_PARENS_RE = re.compile(r"^(.*?)\s*\(([^()]*)\)\s*$")


def parse_collaborator_lines(text: str) -> list[tuple[str, str]]:
    """[(name or 'All', institution)] for each line of a HotCRP `collaborators`
    field: one collaborator per line, "Name (Institution)", where "All
    (Institution)" means everyone there."""
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        match = _TRAILING_PARENS_RE.match(line)
        name, institution = ((match.group(1).strip(), match.group(2).strip()) if match
                             else (line, ""))
        if name:
            lines.append((name, institution))
    return lines


def is_institution_wide(name: str) -> bool:
    return name.strip().lower() == "all"


def declared_names(collaborators: str) -> list[frozenset[str]]:
    """The people (not "All" lines) a `collaborators` field names, as token sets."""
    out = []
    for name, _ in parse_collaborator_lines(collaborators):
        tokens = token_set(name)
        if not is_institution_wide(name) and len(tokens) >= MIN_MATCH_TOKENS:
            out.append(tokens)
    return out


@dataclass(frozen=True)
class CollaboratorConflict:
    author_name: str
    direction: str       # reviewer_declared | author_declared | paper_declared
    evidence: str        # the matched name


def collaborator_conflicts(
    papers: list[dict], reviewers, accounts: hotcrp.Accounts,
) -> dict[int, dict[str, CollaboratorConflict]]:
    """{pid: {reviewer email: CollaboratorConflict}}; the first match per reviewer is kept."""
    profiles = []
    for r in reviewers:
        acct = accounts.by_email.get(r.hotcrp_email)
        profiles.append((r.email, token_set(r.name), declared_names(acct.collaborators if acct else "")))
    derived: dict[int, dict[str, CollaboratorConflict]] = {}
    for paper in papers:
        found: dict[str, CollaboratorConflict] = {}
        for person in hotcrp.paper_people(paper):
            author_email = (person.get("email") or "").strip().lower()
            author_name = hotcrp.person_name(person)
            author_tokens = token_set(author_name)
            author_acct = accounts.by_email.get(author_email) if author_email else None
            author_declared = declared_names(author_acct.collaborators) if author_acct else []
            for email, name_tokens, reviewer_declared in profiles:
                if email in found:
                    continue
                hit = None
                if author_tokens:
                    hit = next((("reviewer_declared", d) for d in reviewer_declared
                                if names_match(d, author_tokens)), None)
                if hit is None and name_tokens:
                    hit = next((("author_declared", d) for d in author_declared
                                if names_match(d, name_tokens)), None)
                if hit is not None:
                    found[email] = CollaboratorConflict(author_name, hit[0], " ".join(sorted(hit[1])))
        for tokens in declared_names(str(paper.get("collaborators") or "")):
            for email, name_tokens, _ in profiles:
                if email not in found and names_match(tokens, name_tokens):
                    found[email] = CollaboratorConflict("", "paper_declared", " ".join(sorted(tokens)))
        if found:
            derived[paper["pid"]] = found
    return derived
