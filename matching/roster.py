"""The reviewer pools, built from the HotCRP user exports alone.

Two pools, decided by which export an account is in: `pc` (the PC export)
and `extra` (the extra-reviewer export). From each account with the `pc`
role, apart from chairs, sysadmins, TRC members and `~~area-chairs`:

  identity  the ORCID, stored as `pid = "orcid:<ORCID>"`, which is how the
            reviewer is found on DBLP. A row in `dblp_placements.csv` names
            the DBLP page by hand (see `load_placements`).
  areas     the topics rated +1/+2, highest first, ties alphabetically: the
            first three are the primary/secondary/tertiary areas and the rest
            `keywords`; together they are the topic document of the
            fingerprint.
  dislikes  the topics rated -1/-2, which the topic veto reads.
  cap       the pool's cap, unless `cap_overrides.csv` sets one.
"""

from __future__ import annotations

import csv
import sys
from dataclasses import dataclass

from . import hotcrp
from .dblp import DBLP_KEY, ORCID_PREFIX, parse_pid

PC = "pc"
EXTRA = "extra"
POOLS = (PC, EXTRA)


@dataclass
class Reviewer:
    email: str            # lowercased; the key everywhere
    hotcrp_email: str     # as HotCRP holds it, for uploads
    first: str
    last: str
    pid: str | None       # "orcid:<ORCID>", "orcid:dblp:<DBLP pid>" or None
    affiliation: str
    primary: str
    secondary: str
    tertiary: str
    keywords: str
    pool: str             # PC or EXTRA
    override_cap: int | None
    disliked_topics: tuple[tuple[str, int], ...] = ()

    @property
    def name(self) -> str:
        return f"{self.first} {self.last}".strip() or self.email


def load_placements(path: str) -> dict[str, str]:
    """{email: DBLP pid} from `dblp_placements.csv` (`email,dblp,note`); {} if absent.

    The chair's decision about which DBLP page is this person's, made by
    reading it. For an account with an ORCID it places that ORCID on the page;
    for one without, it is the only way they are found on DBLP. A blank
    `dblp` cell records a decision not to place someone and is skipped; a
    value that is not a DBLP link fails loudly.
    """
    try:
        f = open(path, newline="", encoding="utf-8")
    except FileNotFoundError:
        return {}
    placements: dict[str, str] = {}
    with f:
        for row in csv.DictReader(f):
            email = (row.get("email") or "").strip().lower()
            raw = (row.get("dblp") or "").strip()
            if not email or not raw:
                continue
            pid = parse_pid(raw)
            if pid is None:
                raise ValueError(f"{path}: {email} has an unparseable DBLP value {raw!r}")
            placements[email] = pid
    return placements


def load_cap_overrides(path: str) -> dict[str, int]:
    """{email: paper cap} from `cap_overrides.csv` (`email,cap,note`); {} if absent."""
    try:
        f = open(path, newline="", encoding="utf-8")
    except FileNotFoundError:
        return {}
    caps: dict[str, int] = {}
    with f:
        for row in csv.DictReader(f):
            email = (row.get("email") or "").strip().lower()
            raw = (row.get("cap") or "").strip()
            if not email or not raw:
                continue
            if not raw.isdigit():
                raise ValueError(f"{path}: {email} has cap {raw!r}; expected a whole number")
            caps[email] = int(raw)
    return caps


def load_reviewers(
    export_path: str, pool: str, *, placements: dict[str, str], cap_overrides: dict[str, int],
) -> list[Reviewer]:
    """The reviewers of one user export, sorted by email."""
    reviewers: list[Reviewer] = []
    bad_orcid: list[str] = []
    no_identity: list[str] = []
    no_topics: list[str] = []
    for acct in sorted(hotcrp.load_export(export_path).pc_accounts, key=lambda a: a.email.lower()):
        email = acct.email.lower()
        if not email or acct.structural_role() is not None:
            continue
        orcid = hotcrp.parse_orcid(acct.orcid)
        if orcid:
            pid = ORCID_PREFIX + orcid
        elif email in placements:
            pid = ORCID_PREFIX + DBLP_KEY + placements[email]
        else:
            pid = None
            (bad_orcid if acct.orcid else no_identity).append(email)
        ranked = acct.liked_topics()
        areas = ranked[:3] + [""] * (3 - len(ranked[:3]))
        if not ranked:
            no_topics.append(email)
        reviewers.append(Reviewer(
            email=email, hotcrp_email=acct.email, first=acct.given, last=acct.family,
            pid=pid, affiliation=acct.affiliation,
            primary=areas[0], secondary=areas[1], tertiary=areas[2],
            keywords=", ".join(ranked[3:]), pool=pool, override_cap=cap_overrides.get(email),
            disliked_topics=tuple(sorted(
                (t, score) for t, score in acct.topic_interests.items() if score < 0)),
        ))

    label = "PC member(s)" if pool == PC else "extra reviewer(s)"
    print(f"{len(reviewers)} {label} loaded from {export_path}", file=sys.stderr)
    if bad_orcid:
        print(f"    {len(bad_orcid)} have an `orcid` cell that is not an ORCID: "
              f"{', '.join(bad_orcid)}", file=sys.stderr)
    if bad_orcid or no_identity:
        print(f"    {len(bad_orcid) + len(no_identity)} have no ORCID and no DBLP placement, "
              f"so their fingerprint is topics only", file=sys.stderr)
    if no_topics:
        print(f"    {len(no_topics)} rated no topic positively: {', '.join(no_topics)}",
              file=sys.stderr)
    return reviewers
