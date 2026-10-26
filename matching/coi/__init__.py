"""Every reason a reviewer is kept off a paper, before any matching happens.

One hit is enough, in any layer, and no phase of the matcher releases it:

  declared      the paper's `pc_conflicts` (what authors and chairs marked in
                HotCRP), against either of the reviewer's addresses.
  own-paper     the reviewer is an author or contact of the paper.
  coauthor      the reviewer wrote a paper with one of its authors in the last
                `coauthor_years` years. By DBLP page where both sides have one
                (the author placed by the ORCID in the authors CSV); by name
                otherwise, erring towards a conflict.
  collaborator  the reviewer's HotCRP `collaborators` name an author, an
                author's name the reviewer, or the submission's own declared
                collaborators name the reviewer.
  institution   the reviewer shares an organisation email domain or a whole
                affiliation name with an author, or works at an institution a
                collaborators line declares in full ("All (Institution)").
  topic-veto    the reviewer rated one of the paper's topics at or below
                `topic_veto` (-2: only "not at all"). A dislike, not a
                conflict of interest, but applied the same way.

Authors could declare conflicts only against accounts on the PC when they
submitted, so extra reviewers have few declared conflicts; the derived
layers are what protect those pairs.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import json

from .. import dblp, hotcrp
from .coauthors import CoauthorConflict, CoauthorIndex, build_coauthor_index, coauthor_conflicts
from .collaborators import CollaboratorConflict, collaborator_conflicts
from .institutions import (
    InstitutionConflict, InstitutionIndex, build_institution_index, institution_conflicts,
)

LAYERS = ("declared", "own-paper", "coauthor", "collaborator", "institution", "topic-veto")
CONFLICTS_HEADER = ("pid", "email", "layer", "author", "evidence")


def topic_veto(papers: list[dict], reviewers, max_score: int) -> dict[int, set[str]]:
    """{pid: reviewer emails} where the reviewer rated a paper topic <= `max_score`."""
    disliked = {r.email: {t.lower() for t, score in r.disliked_topics if score <= max_score}
                for r in reviewers}
    disliked = {email: bad for email, bad in disliked.items() if bad}
    vetoed: dict[int, set[str]] = {}
    for paper in papers:
        topics = {t.lower() for t in paper.get("topics", [])}
        hit = {email for email, bad in disliked.items() if bad & topics}
        if hit:
            vetoed[paper["pid"]] = hit
    return vetoed


@dataclass
class Conflicts:
    """Every layer, per paper."""

    coauthor: dict[int, dict[str, CoauthorConflict]]
    collaborator: dict[int, dict[str, CollaboratorConflict]]
    institution: dict[int, dict[str, InstitutionConflict]]
    vetoed: dict[int, set[str]]
    coauthor_index: CoauthorIndex
    institution_index: InstitutionIndex
    author_pids: dict[str, str]          # paper authors identified on DBLP
    author_unidentified: dict[str, str]  # the rest with an ORCID, and why

    def excluded(self, paper: dict) -> set[str]:
        """Addresses kept off `paper` by any layer. A reviewer is excluded when
        either their address or their HotCRP address is in it."""
        pid = paper["pid"]
        return ({e.lower() for e in paper.get("pc_conflicts", {})}
                | set(hotcrp.own_paper_roles(paper))
                | set(self.coauthor.get(pid, ())) | set(self.collaborator.get(pid, ()))
                | set(self.institution.get(pid, ())) | self.vetoed.get(pid, set()))


def derive(
    papers: list[dict], reviewers: list, accounts: hotcrp.Accounts, *,
    dblp_cache: str, coauthor_years: int, authors_csv: str, author_cache: str, veto_score: int,
) -> Conflicts:
    """Every derived layer over `papers` for `reviewers`.

    `dblp_cache` holds the reviewers' DBLP papers (their co-authors);
    `author_cache` the paper authors' DBLP pages, placed from `authors_csv`.
    Both must exist: running without a layer is worse than not running.
    """
    coauthors, too_narrow = dblp.load_coauthors(dblp_cache, years=coauthor_years)
    short = sorted({r.pid for r in reviewers} & set(too_narrow))
    if short:
        raise ValueError(f"{dblp_cache}: {len(short)} reviewer(s) cached with fewer than "
                         f"{coauthor_years} years of papers; rebuild the fingerprints")
    coauthor_ids, catch_all = dblp.load_coauthor_ids(dblp_cache, years=coauthor_years)
    with open(author_cache, encoding="utf-8") as f:
        author_pids, unidentified = dblp.author_identities(
            hotcrp.load_author_orcids(authors_csv), json.load(f))
    coauthor_index = build_coauthor_index(reviewers, coauthors, coauthor_ids, catch_all,
                                          years=coauthor_years)
    institution_index = build_institution_index(reviewers, accounts)
    return Conflicts(
        coauthor=coauthor_conflicts(papers, coauthor_index, author_pids),
        collaborator=collaborator_conflicts(papers, reviewers, accounts),
        institution=institution_conflicts(papers, institution_index),
        vetoed=topic_veto(papers, reviewers, veto_score),
        coauthor_index=coauthor_index, institution_index=institution_index,
        author_pids=author_pids, author_unidentified=unidentified,
    )


def conflict_rows(
    papers: list[dict], candidates: Sequence[str], reviewers: Mapping[str, object],
    conflicts: Conflicts, veto_score: int,
) -> list[tuple[int, str, str, str, str]]:
    """(pid, email, layer, author, evidence) for every layer that keeps a
    candidate off a paper; a pair two layers exclude appears twice."""
    rows = []
    for paper in sorted(papers, key=lambda p: p["pid"]):
        pid = paper["pid"]
        declared = {e.lower(): str(kind) for e, kind in (paper.get("pc_conflicts") or {}).items()}
        own = hotcrp.own_paper_roles(paper)
        topics = {t.lower() for t in paper.get("topics", [])}
        for email in sorted(candidates):
            r = reviewers[email]
            addresses = (email, r.hotcrp_email.lower())
            kind = next((declared[a] for a in addresses if a in declared), None)
            if kind is not None:
                rows.append((pid, email, "declared", "", kind))
            role = next((own[a] for a in addresses if a in own), None)
            if role is not None:
                rows.append((pid, email, "own-paper", "", role))
            c = conflicts.coauthor.get(pid, {}).get(email)
            if c is not None:
                rows.append((pid, email, "coauthor", c.author_name,
                             f"{c.shared} paper(s) together, latest {c.latest_year}: "
                             f"{c.latest_title} ({c.match} match)"))
            c = conflicts.collaborator.get(pid, {}).get(email)
            if c is not None:
                rows.append((pid, email, "collaborator", c.author_name,
                             f"{c.evidence} ({c.direction.replace('_', ' ')})"))
            c = conflicts.institution.get(pid, {}).get(email)
            if c is not None:
                rows.append((pid, email, "institution", c.author_name or c.author_affiliation,
                             f"{c.evidence} ({c.kind.replace('_', ' ')})"))
            if email in conflicts.vetoed.get(pid, ()):
                rated = [f"{t} ({score})" for t, score in r.disliked_topics
                         if score <= veto_score and t.lower() in topics]
                rows.append((pid, email, "topic-veto", "", "; ".join(rated)))
    return rows


def unexplained_exclusions(
    rows: Sequence[tuple], candidates: Sequence[str], eligible: Mapping[int, list[str]],
) -> tuple[int, int]:
    """(excluded pairs no row explains, rows naming a pair that was not excluded).

    Both 0 exactly when the rows describe the exclusion the matcher applied,
    so a layer added to one and not the other cannot go unnoticed.
    """
    listed: dict[int, set[str]] = defaultdict(set)
    for pid, email, *_ in rows:
        listed[pid].add(email)
    pool = set(candidates)
    unexplained = extra = 0
    for pid, emails in eligible.items():
        excluded = pool - set(emails)
        unexplained += len(excluded - listed[pid])
        extra += len(listed[pid] - excluded)
    return unexplained, extra
