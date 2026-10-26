"""Stage 2: the final reserve reviewers, from the decided list.

The decided list is `reserve_reviewers.csv` with a `Decision` column filled
in by hand. A reserve reviewer is kept when their Decision is Y, they are not
on the PC, and at least one paper that named them is still submitted (in
`inputs.papers`), which matters when a paper is withdrawn after the decisions
were made. A blank Decision counts as N.

For each one kept: their ORCID from the authors CSV (blank when their email
is on no paper), the submissions they authored, and how many papers named
them. Written to `final_reserve_reviewers.csv`.
"""

from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path

from deskreject.people import load_author_orcids
from matching.hotcrp import write_csv_atomically

COLUMNS = ("firstName", "lastName", "email", "orcid", "affiliation", "numSubmittedPapers",
           "numReserveNominations")
NEEDED = ("Decision", "paper", "firstName", "lastName", "email", "affiliation", "onPCList")


def run(decisions_path: str, papers_path: str, authors_path: str, dst: Path) -> int:
    """Write `dst`; returns the number of final reserve reviewers."""
    for p in (decisions_path, papers_path, authors_path):
        if not os.path.exists(p):
            raise FileNotFoundError(f"{p}: not found")
    with open(papers_path, encoding="utf-8") as f:
        papers_data = json.load(f)
    papers = {paper["pid"] for paper in papers_data}
    # Papers each author email is on, compared case-insensitively.
    papers_by_author: dict[str, set[int]] = {}
    for paper in papers_data:
        for author in paper["authors"]:
            papers_by_author.setdefault(author.get("email", "").strip().lower(),
                                        set()).add(paper["pid"])
    with open(decisions_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = set(NEEDED) - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"{decisions_path}: needs the column(s) {', '.join(sorted(missing))}")
        reviewers = list(reader)
    email_to_orcid = load_author_orcids(authors_path)

    out, undecided = [], 0
    for row in reviewers:
        decision = row["Decision"].strip()
        undecided += not decision
        named = {int(i) for i in row["paper"].split(";")}
        nominated = named & papers
        if decision != "Y" or row["onPCList"].strip() != "no" or not nominated:
            continue
        email = row["email"].strip().lower()
        # Papers that named them count as authored: some reviewers used another
        # email on their papers.
        authored = papers_by_author.get(email, set()) | nominated
        out.append((row["firstName"], row["lastName"], row["email"], email_to_orcid.get(email, ""),
                    row["affiliation"], len(authored), len(nominated)))
    write_csv_atomically(str(dst), COLUMNS, out)
    print(f"[finalise] {len(reviewers)} reserve reviewer(s) decided on, {len(out)} final; "
          f"{sum(1 for r in out if not r[3])} without an ORCID", file=sys.stderr)
    if undecided:
        print(f"WARNING: {undecided} row(s) have no Decision and count as N", file=sys.stderr)
    return len(out)
