"""Small builders for test data."""

from __future__ import annotations

import csv
from pathlib import Path

from matching.roster import PC, Reviewer

EXPORT_FIELDS = ["given_name", "family_name", "email", "affiliation", "orcid", "country",
                 "roles", "tags", "collaborators", "topic: Storage", "topic: Networks",
                 "topic: Security", "disabled"]


def reviewer(email: str, *, pool: str = PC, name: str = "Test Person", pid: str | None = None,
             affiliation: str = "", hotcrp_email: str = "", disliked=(), cap=None) -> Reviewer:
    first, _, last = name.partition(" ")
    return Reviewer(email=email, hotcrp_email=hotcrp_email or email, first=first, last=last,
                    pid=pid, affiliation=affiliation, primary="Storage", secondary="",
                    tertiary="", keywords="", pool=pool, override_cap=cap,
                    disliked_topics=tuple(disliked))


def account(email: str, **fields) -> dict:
    row = {f: "" for f in EXPORT_FIELDS}
    row.update(given_name="Test", family_name=email.split("@")[0].title(), email=email, roles="pc")
    row.update(fields)
    return row


def write_export(path: Path, rows: list[dict]) -> str:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=EXPORT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return str(path)


def paper(pid: int, *, topics=("Storage",), authors=(), contacts=(), pc_conflicts=None,
          collaborators: str = "") -> dict:
    return {"pid": pid, "title": f"Paper {pid}", "abstract": "An abstract.", "status": "submitted",
            "topics": list(topics), "authors": list(authors), "contacts": list(contacts),
            "pc_conflicts": pc_conflicts or {}, "collaborators": collaborators,
            "submitted_at": 1_780_000_000}


def author(email: str, name: str = "Ann Author", affiliation: str = "") -> dict:
    first, _, last = name.partition(" ")
    return {"email": email, "given_name": first, "family_name": last, "affiliation": affiliation}
