"""Conflicts from HotCRP's Assignments -> Conflicts page, as a bulk upload.

The page lists the potential conflicts HotCRP found itself (author names,
affiliations, collaborators): each reviewer as a heading row
(`<td class="plheading">Name <span class="auaff">(Affiliation)</span>`)
followed by one row per paper (`<tr class="pl ..." data-pid="1356">`) with
the conflict radio buttons. Every row whose checked button is not already a
conflict becomes one `conflict` row of the upload.

The page shows a reviewer's email only when two accounts share their name
(`Name <email>`). Otherwise the reviewer is found by name in the user export
of the same HotCRP site, which holds exactly the names the page shows. A
reviewer not found is reported and left out.
"""

from __future__ import annotations

import csv
import html
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

from matching.hotcrp import write_csv_atomically

UPLOAD_HEADER = ("paper", "action", "email", "conflict")

ROW_RE = re.compile(
    r'<td class="plheading"[^>]*>(?P<name>[^<]*)'
    r'|<tr class="pl k\d" data-pid="(?P<pid>\d+)"(?P<row>.*?)</tr>', re.DOTALL)
EMAIL_RE = re.compile(r"<([^<>@\s]+@[^<>\s]+)>")
CHECKED_RE = re.compile(r'<input type="radio"[^>]*value="([^"]+)"[^>]*checked')


def clean(text: str) -> str:
    return " ".join(html.unescape(text).split())


def write_upload(path: Path, pairs) -> None:
    """HotCRP's bulk conflict upload, one row per (paper, email) pair."""
    write_csv_atomically(str(path), UPLOAD_HEADER,
                         ((paper, "conflict", email, "yes") for paper, email in pairs))


def load_accounts(path: str) -> dict[str, list[str]]:
    """{name in lower case: [emails]} of a user export."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        accounts = defaultdict(list)
        for row in csv.DictReader(f):
            name = f"{row['given_name'].strip()} {row['family_name'].strip()}".lower()
            accounts[name].append(row["email"].strip())
    return accounts


def run(page_path: str, export_path: str, dst: Path) -> int:
    """Write `dst`; returns the number of conflicts written."""
    for p in (page_path, export_path):
        if not os.path.exists(p):
            raise FileNotFoundError(f"{p}: not found")
    with open(page_path, encoding="utf-8") as f:
        page = f.read()
    accounts = load_accounts(export_path)
    pairs, unmatched, already, email = [], set(), 0, None
    reviewers = 0
    for match in ROW_RE.finditer(page):
        if match["name"] is not None:
            reviewers += 1
            name = clean(match["name"])
            shown = EMAIL_RE.search(name)
            found = [shown[1]] if shown else accounts.get(name.lower(), [])
            email = found[0] if len(found) == 1 else None
            if email is None:
                unmatched.add(name)
            continue
        checked = CHECKED_RE.findall(match["row"])
        if checked and checked[0] != "nonconflict":
            already += 1
            continue
        if email is not None:
            pairs.append((int(match["pid"]), email))
    if not reviewers:
        raise ValueError(f"{page_path}: no reviewer on the page; is it the Assignments -> "
                         f"Conflicts page, saved as HTML?")
    write_upload(dst, pairs)
    print(f"[pages] {os.path.basename(page_path)}: {reviewers} reviewer(s), {len(pairs)} "
          f"conflict(s) to upload{f', {already} already confirmed' if already else ''}",
          file=sys.stderr)
    if unmatched:
        print(f"WARNING: {len(unmatched)} reviewer(s) on the page not found in "
              f"{os.path.basename(export_path)}, their rows left out: "
              f"{', '.join(sorted(unmatched))}", file=sys.stderr)
    return len(pairs)
