"""Reading HotCRP's downloads and writing its upload files.

Inputs (see README.md for where each comes from):

  user export   Users -> Program committee -> Download -> PC info. One for
                the PC members, one for the extra reviewers. Read by
                `load_export`/`load_exports`.
  paper JSON    Search -> all -> Download -> JSON, downloaded as chair so it
                carries `pc_conflicts`. Read by `load_papers`.
  authors CSV   Search -> all -> Download -> Authors: each author's ORCID and
                country. Read by `load_author_orcids` and friends.

Output: the bulk-assignment CSV HotCRP takes under Assignments -> Upload
(`write_upload`).
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
import tempfile
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

# HotCRP's per-topic interest columns in the user export: `topic: <name>`,
# holding the -2..2 score the account set.
TOPIC_PREFIX = "topic: "

UPLOAD_HEADER = ("paper", "action", "email", "round")

_ORCID_RE = re.compile(r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$")


def parse_orcid(value: str) -> str | None:
    """The bare ORCID from a cell or URL, or None.

    HotCRP marks an ORCID typed in rather than authenticated with a trailing
    `?`; it is accepted, being the person's own claim.
    """
    value = (value or "").strip().rstrip("?")
    if not value:
        return None
    value = value.rsplit("/", 1)[-1].upper()
    return value if _ORCID_RE.match(value) else None


def tag_names(tags: str) -> set[str]:
    """Bare tag names: HotCRP writes `~~chair-only` and `tag#value` forms of one name."""
    return {t.split("#", 1)[0].lstrip("~").lower() for t in tags.split()}


class Account:
    """One row of a HotCRP user export."""

    def __init__(self, row: dict[str, str]) -> None:
        self.given = row.get("given_name", "").strip()
        self.family = row.get("family_name", "").strip()
        self.email = row.get("email", "").strip()
        self.affiliation = row.get("affiliation", "").strip()
        self.orcid = row.get("orcid", "").strip()
        self.country = row.get("country", "").strip()
        self.roles = row.get("roles", "").strip()
        self.tags = row.get("tags", "").strip()
        self.collaborators = row.get("collaborators", "").strip()
        self.disabled = row.get("disabled", "").strip().lower() == "yes"
        self.topic_interests: dict[str, int] = {}
        for header, value in row.items():
            if not header or not header.startswith(TOPIC_PREFIX) or not (value or "").strip():
                continue
            try:
                self.topic_interests[header[len(TOPIC_PREFIX):]] = int(value.strip())
            except ValueError:
                continue

    @property
    def is_pc(self) -> bool:
        """On the PC and able to log in."""
        return "pc" in self.roles.split() and not self.disabled

    def structural_role(self) -> str | None:
        """'chair', 'trc', 'sysadmin' or 'area-chair' for an account on the PC
        for a reason other than reviewing; None for a reviewer."""
        tags = self.tags.split()
        if "chairs" in tags or "chair" in self.roles.split():
            return "chair"
        if any(t.startswith("trc") for t in tags):
            return "trc"
        if "sysadmin" in self.roles.split():
            return "sysadmin"
        if any(t.endswith("area-chairs") for t in tags):
            return "area-chair"
        return None

    def liked_topics(self) -> list[str]:
        """Topics rated +1 or +2, highest first, ties alphabetically."""
        return sorted((t for t, score in self.topic_interests.items() if score >= 1),
                      key=lambda t: (-self.topic_interests[t], t))


class Accounts:
    """The accounts of one or more user exports, by lowercased email."""

    def __init__(self, accounts: list[Account]) -> None:
        self.accounts = accounts
        self.by_email: dict[str, Account] = {}
        for acct in accounts:
            if acct.email:
                self.by_email.setdefault(acct.email.lower(), acct)

    @property
    def pc_accounts(self) -> list[Account]:
        return [a for a in self.accounts if a.is_pc]


def load_export(path: str) -> Accounts:
    """One user export. A file with no `pc` account at all is refused: a
    truncated or wrong download would otherwise empty the roster silently."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"{path}: user export not found (HotCRP Users -> Program "
                                f"committee -> Download -> PC info)")
    with open(path, newline="", encoding="utf-8-sig") as f:
        accounts = Accounts([Account(row) for row in csv.DictReader(f)])
    if not accounts.pc_accounts:
        raise ValueError(f"{path}: no account has the 'pc' role; the export looks truncated "
                         f"or is the wrong file")
    return accounts


def load_exports(paths: Sequence[str]) -> Accounts:
    """Both user exports as one index, for the conflict and country layers that
    look a person up by address. An address in both files is refused: the
    file decides whether someone is PC or an extra reviewer."""
    exports = [load_export(p) for p in paths]
    seen: set[str] = set()
    both: set[str] = set()
    for export in exports:
        both |= seen & export.by_email.keys()
        seen |= export.by_email.keys()
    if both:
        raise ValueError(f"{len(both)} address(es) are in more than one user export "
                         f"({', '.join(paths)}): {', '.join(sorted(both))}")
    return Accounts([a for export in exports for a in export.accounts])


def load_papers(path: str, exclude: frozenset[int] = frozenset()) -> list[dict]:
    """The submitted papers of the paper JSON, minus any pid in `exclude`."""
    with open(path, encoding="utf-8") as f:
        papers = json.load(f)
    selected = [p for p in papers
                if p["pid"] not in exclude and str(p.get("status") or "") == "submitted"]
    if len(selected) < len(papers):
        print(f"Skipping {len(papers) - len(selected)} papers (not submitted, or excluded); "
              f"{len(selected)} of {len(papers)} remain", file=sys.stderr)
    return selected


def own_paper_roles(paper: dict) -> dict[str, str]:
    """{email: 'author' or 'contact'} for the people on a paper."""
    roles: dict[str, str] = {}
    for role, people in (("author", paper.get("authors")), ("contact", paper.get("contacts"))):
        for person in people or []:
            email = (person.get("email") or "").strip().lower()
            if email:
                roles.setdefault(email, role)
    return roles


def paper_people(paper: dict) -> list[dict]:
    return (paper.get("authors") or []) + (paper.get("contacts") or [])


def person_name(person: dict) -> str:
    return f"{person.get('given_name', '')} {person.get('family_name', '')}".strip()


def load_author_orcids(path: str) -> dict[str, tuple[str, str]]:
    """{author email: (ORCID, HotCRP name)} from the authors CSV.

    An address listed with two different ORCIDs is left out (matched by name).
    """
    seen: dict[str, set[tuple[str, str]]] = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            email = (row.get("email") or "").strip().lower()
            orcid = parse_orcid(row.get("orcid") or "")
            if email and orcid:
                name = f"{row.get('given_name', '')} {row.get('family_name', '')}".strip()
                seen.setdefault(email, set()).add((orcid, name))
    return {e: min(v) for e, v in seen.items() if len({o for o, _ in v}) == 1}


def load_paper_author_orcids(path: str) -> dict[int, set[str]]:
    """{paper: its authors' ORCIDs} from the authors CSV (non-author contacts left out)."""
    out: dict[int, set[str]] = defaultdict(set)
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            orcid = parse_orcid(row.get("orcid") or "")
            if orcid and (row.get("iscontact") or "").strip().lower() != "nonauthor":
                out[int(row["paper"])].add(orcid)
    return dict(out)


def write_csv_atomically(path: str, header: Sequence[str], rows: Iterable[Sequence]) -> None:
    """Write `header` then `rows` to `path` via a temporary file and a rename."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", newline="", encoding="utf-8", dir=target.parent,
            prefix=f".{target.name}.", suffix=".tmp", delete=False,
        ) as f:
            temporary = Path(f.name)
            writer = csv.writer(f)
            writer.writerow(header)
            writer.writerows(rows)
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_upload(
    path: str,
    slates: Mapping[int, Sequence[str]],
    affinity: Mapping[tuple[str, int], float],
    reviewers: Mapping[str, object],
    clear_emails: Sequence[str] | None = None,
) -> None:
    """The slates as a HotCRP bulk-assignment upload of round R1 primary reviews.

    It first clears every R1 review (`all,clearreview,all,R1`), or with
    `clear_emails` only those reviewers' (`all,clearreview,<email>,R1`), so an
    upload replaces rather than adds. Each paper's reviewers are listed best
    match first, under the address HotCRP holds the account under.
    """
    rows: list[list] = []
    if clear_emails is None:
        rows.append(["all", "clearreview", "all", "R1"])
    else:
        rows += [["all", "clearreview", e, "R1"]
                 for e in sorted({reviewers[e].hotcrp_email for e in clear_emails})]
    for pid in sorted(slates):
        for email in sorted(slates[pid], key=lambda e: (-affinity[(e, pid)], e)):
            rows.append([pid, "primaryreview", reviewers[email].hotcrp_email, "R1"])
    write_csv_atomically(path, UPLOAD_HEADER, rows)


def load_upload_pairs(path: str) -> dict[int, set[str]]:
    """{pid: reviewer emails, lowercased} from the `primaryreview` rows of an upload."""
    pairs: dict[int, set[str]] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["action"] == "primaryreview":
                pairs.setdefault(int(row["paper"]), set()).add(row["email"].strip().lower())
    return pairs
