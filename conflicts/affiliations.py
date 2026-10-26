"""Same-institution conflicts of the extra reviewers, as a bulk upload.

The extra reviewers were added after submission, so authors declared few
conflicts with them. Every (extra reviewer, paper) pair is checked with
institutions.py: the same affiliation or the same email domain as one of the
paper's authors is a conflict. Those HotCRP does not have yet (the extra
reviewers' conflicts CSV) are the upload; every conflict found, with which
signal found it, is the evidence for checking.
"""

from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path

from matching.hotcrp import write_csv_atomically

from .institutions import email_domain, normalize, search_terms, term_pattern
from .pages import write_upload

EVIDENCE_HEADER = ("paper", "email", "sameAffiliation", "sameEmailDomain", "hotcrpMarked")


def yes_no(value: bool) -> str:
    return "yes" if value else "no"


def run(export_path: str, pcconflicts_path: str, papers_path: str,
        terms: dict[str, list[str]], dst: Path, evidence_dst: Path) -> int:
    """Write `dst` and `evidence_dst`; returns the number of conflicts to upload."""
    for p in (export_path, pcconflicts_path, papers_path):
        if not os.path.exists(p):
            raise FileNotFoundError(f"{p}: not found")
    with open(export_path, newline="", encoding="utf-8-sig") as f:
        reviewers = list(csv.DictReader(f))
    with open(pcconflicts_path, newline="", encoding="utf-8-sig") as f:
        marked = {(int(row["paper"]), row["email"].strip().lower()) for row in csv.DictReader(f)}
    with open(papers_path, encoding="utf-8") as f:
        papers = json.load(f)

    # Normalised affiliations and email domains of each paper's authors.
    paper_affiliations = {
        paper["pid"]: [normalize(a.get("affiliation", "")) for a in paper["authors"]]
        for paper in papers}
    paper_domains = {
        paper["pid"]: {email_domain(a.get("email", "")) for a in paper["authors"]} - {""}
        for paper in papers}

    found = []
    for reviewer in reviewers:
        email = reviewer["email"]
        patterns = [term_pattern(t) for t in search_terms(reviewer["affiliation"].strip(), terms)]
        domain = email_domain(email)
        for pid, affiliations in paper_affiliations.items():
            same_affiliation = any(p.search(a) for a in affiliations for p in patterns)
            same_domain = domain in paper_domains[pid]
            if same_affiliation or same_domain:
                found.append((pid, email, yes_no(same_affiliation), yes_no(same_domain),
                              yes_no((pid, email.strip().lower()) in marked)))
    found.sort(key=lambda r: (r[1], r[0]))
    write_csv_atomically(str(evidence_dst), EVIDENCE_HEADER, found)
    new = [(pid, email) for pid, email, _, _, hotcrp in found if hotcrp == "no"]
    write_upload(dst, new)
    print(f"[affiliations] {len(reviewers)} extra reviewer(s) x {len(papers)} papers: "
          f"{len(found)} conflict(s) (same affiliation "
          f"{sum(r[2] == 'yes' for r in found)}, same email domain "
          f"{sum(r[3] == 'yes' for r in found)}); {len(found) - len(new)} already in HotCRP, "
          f"{len(new)} to upload for {len({e for _, e in new})} reviewer(s)", file=sys.stderr)
    return len(new)
