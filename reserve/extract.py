"""Stage 1: every reserve reviewer the papers named at the deadline, one row per person.

Each paper's reserve reviewer field is parsed (parse.py). A person named by
several papers is kept once, listing all their papers: the same person is
recognised by ORCID (one person may give two emails), otherwise by email
compared case-insensitively. Rows with neither are kept as they are.
Reviewers who match no author of their paper come first, for checking.

Each person is marked as on the PC or not (by ORCID, then email). Anyone a
desk-rejected paper named is left out, even if another paper named them too,
and a PC member among them is listed in `pc_to_remove.csv`, to come off the
PC in HotCRP. The rest get the hints of lookups.py; their list
(`reserve_reviewers.csv`) is what a person decides on.
"""

from __future__ import annotations

import csv
import json
import os
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from deskreject.people import load_author_orcids, load_pc
from matching.hotcrp import write_csv_atomically

from . import lookups, parse

COLUMNS = ("paper", "firstName", "lastName", "email", "affiliation", "website", "emailStatus",
           "onPCList", "sector", "hasPhD", "orcidPublications", "openalexPublications")


def merge(rows: list[dict], email_to_orcid: dict[str, str]) -> list[dict]:
    """One row per person, in order of first appearance, unmatched ones first."""
    people: dict[str, dict] = {}
    for index, row in enumerate(rows):
        email = row["email"].strip().lower()
        key = email_to_orcid.get(email) or email or f"row {index}"
        if key not in people:
            people[key] = dict(row)
        else:
            person = people[key]
            person["paper"] += "; " + row["paper"]
            person["emailStatus"] = min(person["emailStatus"], row["emailStatus"],
                                        key=parse.EMAIL_STATUS_ORDER.index)
    merged = list(people.values())
    return ([r for r in merged if r["emailStatus"] == "unmatched"]
            + [r for r in merged if r["emailStatus"] != "unmatched"])


def pc_to_remove(removed: list[dict], email_to_orcid: dict[str, str],
                 pcinfo_path: str) -> list[tuple[str, str, str, str]]:
    """(given name, family name, email, desk-rejected papers) of each PC member
    a desk-rejected paper named, matched by ORCID and then by email."""
    with open(pcinfo_path, newline="", encoding="utf-8-sig") as f:
        accounts = list(csv.DictReader(f))
    out = []
    for account in accounts:
        orcid = account["orcid"].strip().rstrip("?")
        email = account["email"].strip().lower()
        papers = [r["paper"] for r in removed if r["email"].strip().lower() == email
                  or (orcid and email_to_orcid.get(r["email"].strip().lower()) == orcid)]
        if papers:
            out.append((account["given_name"], account["family_name"], account["email"],
                        "; ".join(papers)))
    return out


def run(papers_path: str, field: str, desk_rejected: set[int], authors_path: str,
        pcinfo_path: str, cache: Path, dst: Path, pc_dst: Path) -> int:
    """Write `dst` and `pc_dst`; returns the number of reserve reviewers."""
    for p in (papers_path, authors_path, pcinfo_path):
        if not os.path.exists(p):
            raise FileNotFoundError(f"{p}: not found")
    with open(papers_path, encoding="utf-8") as f:
        papers = json.load(f)
    rows, skipped, no_field = [], Counter(), 0
    for paper in papers:
        raw = paper.get(field)
        if not raw:
            no_field += 1
            continue
        row = parse.parse(raw, paper)
        if row is None:
            print(f"[extract] paper #{paper['pid']} names nobody: {raw!r}", file=sys.stderr)
            skipped[parse.skip_reason(raw)] += 1
            continue
        rows.append({"paper": str(paper["pid"]), **row})
    if no_field == len(papers):
        raise ValueError(f"{papers_path}: no paper has the field {field!r}; check "
                         f"reserve.field")

    email_to_orcid = load_author_orcids(authors_path)
    merged = merge(rows, email_to_orcid)
    pc_orcids, pc_emails = load_pc(pcinfo_path)
    for person in merged:
        email = person["email"].strip().lower()
        person["onPCList"] = ("yes" if email_to_orcid.get(email) in pc_orcids
                              or email in pc_emails else "no")
    # Anyone a desk-rejected paper named is dropped, even if another paper named
    # them too; a PC member among them comes off the PC.
    gone = lambda r: bool({int(i) for i in r["paper"].split("; ")} & desk_rejected)
    removed = [r for r in merged if gone(r)]
    people = [r for r in merged if not gone(r)]
    pc_gone = pc_to_remove([r for r in removed if r["onPCList"] == "yes"], email_to_orcid,
                           pcinfo_path)

    orcids = [email_to_orcid.get(r["email"].strip().lower()) for r in people]
    with ThreadPoolExecutor(max_workers=8) as pool:
        sectors = list(pool.map(lambda r: lookups.sector(r["affiliation"], r["email"], cache),
                                people))
        records = list(pool.map(lambda o: lookups.orcid_record(o, cache), orcids))
    # OpenAlex rate-limits parallel requests, so it is asked one at a time.
    openalex = [lookups.openalex_publications(o, cache) for o in orcids]
    for person, sec, record, works in zip(people, sectors, records, openalex):
        person.update(sector=sec, hasPhD=lookups.has_phd(record),
                      orcidPublications=lookups.orcid_publications(record),
                      openalexPublications=works)
    write_csv_atomically(str(dst), COLUMNS, ([r[c] for c in COLUMNS] for r in people))
    write_csv_atomically(str(pc_dst), ("given_name", "family_name", "email", "paper"), pc_gone)

    missing = [r["paper"] for r in people if not r["email"] or not r["lastName"]]
    print(f"[extract] {len(papers)} papers: {no_field} without the field, "
          f"{sum(skipped.values())} naming nobody "
          f"({', '.join(f'{k} {n}' for k, n in skipped.most_common()) or '-'}), "
          f"{len(rows)} naming someone", file=sys.stderr)
    print(f"[extract] {len(merged)} reserve reviewer(s) after merging {len(rows) - len(merged)} "
          f"duplicate(s); {len(removed)} named by a desk-rejected paper left out, "
          f"{len(people)} kept", file=sys.stderr)
    print(f"[extract] of those kept: on the PC {sum(r['onPCList'] == 'yes' for r in people)}; "
          f"email {dict(Counter(r['emailStatus'] for r in people))}; "
          f"with an ORCID {sum(o is not None for o in orcids)}", file=sys.stderr)
    if pc_gone:
        print(f"WARNING: {len(pc_gone)} PC member(s) were named by a desk-rejected paper; "
              f"remove them from the PC in HotCRP and download the PC export again "
              f"(see {pc_dst.name})", file=sys.stderr)
    if missing:
        print(f"WARNING: {len(missing)} reserve reviewer(s) without a name or email, named by "
              f"paper(s) {', '.join(missing)}", file=sys.stderr)
    return len(people)
