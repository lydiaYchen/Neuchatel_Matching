"""The papers a person should look at for a desk reject, with the reasons.

Three rules, each flagging a paper with a reason; a person decides:

  (i)   submissions  an author on more than `max_submissions` papers. Authors
                     are one person by ORCID (looked up by email in the
                     authors CSV), otherwise by email, otherwise by name.
  (ii)  reserve      no reserve reviewer with an email address, and no valid
                     exemption: an author on the PC, or the paper on the
                     chairs' list of approved exemptions.
  (iii) format       too many pages, too small a body font, or margins too
                     small by more than `margin_over` percent. The paper JSON
                     is newer than the format check, so a problem only counts
                     if the JSON still reports it.

Written to `flagged.csv` (`paper,reason`), one row per flagged paper.
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from matching.hotcrp import write_csv_atomically

from .people import load_author_orcids, load_pc

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# The start of each option of the "Reserve reviewer type" field on the EuroSys
# submission form; change them if the form changes.
TYPE_FIELD = "Reserve reviewer type"
PC_EXEMPT_TYPE = "One of the authors is currently serving on the EuroSys"
CHAIR_EXEMPT_TYPE = "We have contacted the PC chairs"
RESERVE_REVIEWER_TYPE = "The above reserve reviewer"
EXEMPT_LIKE_RE = re.compile(r"^(exempt|none|noen|n/?a)\b", re.IGNORECASE)

# Format check message prefix -> field name in the JSON `format_problem_fields`.
FORMAT_FIELDS = {
    "Too many pages": "pagelimit",
    "Body font too small": "bodyfontsize",
    "Margins too small": "textblock",
}
# Labels for fields whose JSON name is unclear.
FORMAT_LABELS = {"textblock": "margin"}
LABELS = ("(i) too many submissions", "(ii) claim author is PC but not on PC list",
          "(ii) exemption but not on exemption list", "(ii) incomplete reserve reviewer info",
          "(ii) exempt without reason", "(ii) no reserve reviewer", "(iii) pagelimit",
          "(iii) bodyfontsize", "(iii) margin", "(iii) missing from format check")


def is_serious(message: str, margin_over: int) -> bool:
    if message.startswith(("Too many pages", "Body font too small")):
        return True
    if message.startswith("Margins too small"):
        pct = re.search(r"by (?:up to )?(\d+)%", message)
        return pct is not None and int(pct.group(1)) > margin_over
    return False


def load_formatcheck(path: str) -> dict[int, str]:
    """{paper: messages} from HotCRP's format check CSV."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        return {int(row["paper"]): row["messages"] or "" for row in csv.DictReader(f)}


def run(cfg: dict, papers_path: str, authors_path: str, pcinfo_path: str, formatcheck_path: str,
        exempted: set[int], dst: Path) -> int:
    """Write `dst`; returns the number of flagged papers."""
    for p in (papers_path, authors_path, pcinfo_path, formatcheck_path):
        if not os.path.exists(p):
            raise FileNotFoundError(f"{p}: not found")
    with open(papers_path, encoding="utf-8") as f:
        papers = json.load(f)
    email_to_orcid = load_author_orcids(authors_path)
    pc_orcids, pc_emails = load_pc(pcinfo_path)
    formatcheck = load_formatcheck(formatcheck_path)

    reasons: dict[int, list[str]] = defaultdict(list)
    passed = Counter()
    flagged_by_label: dict[str, set[int]] = defaultdict(set)

    def flag(pid: int, label: str, reason: str) -> None:
        reasons[pid].append(reason)
        flagged_by_label[label].add(pid)

    # (i) Too many submissions.
    papers_by_author: dict[str, set[int]] = defaultdict(set)
    names_by_author: dict[str, str] = {}
    for paper in papers:
        for author in paper["authors"]:
            email = author.get("email", "").strip().lower()
            name = f"{author.get('given_name', '')} {author.get('family_name', '')}".strip()
            key = email_to_orcid.get(email) or email or name.lower()
            papers_by_author[key].add(paper["pid"])
            names_by_author.setdefault(key, name)
    orcids = set(email_to_orcid.values())
    for key, pids in papers_by_author.items():
        if len(pids) > cfg["max_submissions"]:
            id_label = f"ORCID {key}" if key in orcids else key
            for pid in pids:
                flag(pid, "(i) too many submissions",
                     f"Too many submissions: {names_by_author[key]} ({id_label}) "
                     f"is an author on {len(pids)} papers {sorted(pids)}")

    def has_pc_author(paper: dict) -> bool:
        """An author on the PC, matched by ORCID first and then by email."""
        for author in paper["authors"]:
            email = author.get("email", "").strip().lower()
            if email_to_orcid.get(email) in pc_orcids or email in pc_emails:
                return True
        return False

    # (ii) Reserve reviewer. A valid entry has an email address; otherwise the
    # paper is exempt only if an author is on the PC or the chairs approved it.
    for paper in papers:
        pid = paper["pid"]
        reserve = (paper.get(cfg["field"]) or "").strip()
        reserve_type = paper.get(TYPE_FIELD) or ""
        if EMAIL_RE.search(reserve):
            continue
        if reserve_type.startswith(PC_EXEMPT_TYPE):
            if has_pc_author(paper):
                passed["verified PC author"] += 1
            else:
                flag(pid, "(ii) claim author is PC but not on PC list",
                     f"Reserve reviewer: wrote {reserve or 'nothing'!r} claiming a PC "
                     "member author, but no author is on the PC list")
            continue
        if pid in exempted:
            passed["approved exemption"] += 1
            continue
        # Writing "exempt" while choosing the reserve reviewer type is treated the
        # same as claiming the chairs' approval.
        if reserve_type.startswith(CHAIR_EXEMPT_TYPE) or (
                reserve_type.startswith(RESERVE_REVIEWER_TYPE) and EXEMPT_LIKE_RE.match(reserve)):
            flag(pid, "(ii) exemption but not on exemption list",
                 f"Reserve reviewer: wrote {reserve or 'nothing'!r} claiming an exemption, "
                 "but paper is not on the exemption list")
        elif reserve and not EXEMPT_LIKE_RE.match(reserve):
            flag(pid, "(ii) incomplete reserve reviewer info",
                 f"Reserve reviewer: incomplete entry {reserve!r} (no email), double check")
        elif reserve:
            flag(pid, "(ii) exempt without reason",
                 f"Reserve reviewer: wrote {reserve!r} without a valid exemption reason")
        else:
            flag(pid, "(ii) no reserve reviewer",
                 "Reserve reviewer: no reserve reviewer and no exemption reason")

    # (iii) Format.
    for paper in papers:
        pid = paper["pid"]
        current_fields = set(paper["submission"].get("format_problem_fields", []))
        if pid not in formatcheck:
            unchecked = current_fields & {"pagelimit", "bodyfontsize"}
            if unchecked:
                flag(pid, "(iii) missing from format check",
                     f"Format: {', '.join(sorted(unchecked))} flagged but paper is "
                     "missing from the format check CSV, double check")
            continue
        for message in formatcheck[pid].splitlines():
            if not is_serious(message, cfg["margin_over"]):
                continue
            field = next((f for prefix, f in FORMAT_FIELDS.items() if message.startswith(prefix)),
                         None)
            if field not in current_fields:
                print(f"[deskreject] paper #{pid} fixed since the format check: {message}",
                      file=sys.stderr)
                passed["fixed since the format check"] += 1
                continue
            flag(pid, f"(iii) {FORMAT_LABELS.get(field, field)}", f"Format: {message}")

    write_csv_atomically(str(dst), ("paper", "reason"),
                         ((pid, "; ".join(r)) for pid, r in sorted(reasons.items())))
    print(f"[deskreject] {len(papers)} papers, {len(reasons)} flagged:", file=sys.stderr)
    for label in LABELS:
        print(f"[deskreject]   {label}: {len(flagged_by_label[label])}", file=sys.stderr)
    print(f"[deskreject] passed: {', '.join(f'{k} {n}' for k, n in passed.items()) or '-'}",
          file=sys.stderr)
    return len(reasons)
