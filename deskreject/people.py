"""Authors' ORCIDs and the PC, as the desk-reject check and the reserve
reviewers both look people up."""

from __future__ import annotations

import csv


def load_author_orcids(path: str) -> dict[str, str]:
    """{email (lower case): ORCID} from the authors CSV. The paper JSON carries no
    ORCIDs, so an author's is looked up by email here."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        return {row["email"].strip().lower(): row["orcid"].strip()
                for row in csv.DictReader(f) if row["email"] and row["orcid"]}


def load_pc(path: str) -> tuple[set[str], set[str]]:
    """(ORCIDs, emails in lower case) of the PC user export."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    # HotCRP ends an ORCID typed in rather than authenticated with "?".
    orcids = {row["orcid"].strip().rstrip("?") for row in rows if row["orcid"]} - {""}
    emails = {row["email"].strip().lower() for row in rows if row["email"]}
    return orcids, emails
