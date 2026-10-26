"""Each reviewer's publication record and conflicts, as a run used them (stage 4).

    uv run python -m matching.records runs/<name>                          # rewrite the CSVs
    uv run python -m matching.records runs/<name> --email someone@uni.edu  # print one record

Writes, in the run directory:

  reviewers.csv     one row per PC member and extra reviewer: ORCID, DBLP page
                    and how it was found, what the fingerprint rests on
                    (`basis`), papers embedded and how many had an abstract,
                    papers each conflict layer kept them off
                    (`conflicted_papers` counts conflicts of interest, not the
                    topic veto), papers assigned.
  publications.csv  every paper a fingerprint embeds: exactly what SPECTER2 saw.

From `fingerprint_basis.json`/`extra_fingerprint_basis.json`, `conflicts.csv`
and `assignment.csv`. With --email it prints the record instead: the papers,
every conflict with its evidence, the papers assigned.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

from . import hotcrp
from .coi import LAYERS
from .config import RunDir

# The topic veto keeps a reviewer off a paper like a conflict does, but it is
# their own dislike, not a conflict of interest.
COI_LAYERS = LAYERS[:-1]
REVIEWERS_HEADER = (
    "email", "name", "pool", "orcid", "dblp", "dblp_name", "placed_by", "basis",
    "years", "papers", "with_abstract", "areas", "conflicted_papers",
    *(layer.replace("-", "_") for layer in LAYERS), "assigned",
)
PUBLICATIONS_HEADER = ("email", "name", "pool", "year", "title", "doi", "abstract")


def load(rd: RunDir) -> tuple[dict[str, dict], dict[str, list[dict]], dict[str, list[int]]]:
    """(basis by email, conflict rows by email, assigned pids by email)."""
    basis: dict[str, dict] = {}
    for pool in ("pc", "extra"):
        with open(rd.basis[pool], encoding="utf-8") as f:
            basis.update(json.load(f))
    conflicts: dict[str, list[dict]] = defaultdict(list)
    with open(rd.conflicts, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            conflicts[row["email"]].append({**row, "pid": int(row["pid"])})
    assigned: dict[str, list[int]] = defaultdict(list)
    pairs = hotcrp.load_upload_pairs(str(rd.upload))
    for pid in sorted(pairs):
        for email in pairs[pid]:
            assigned[email].append(pid)
    return basis, conflicts, assigned


def by_newest(publications: list) -> list:
    return sorted(publications, key=lambda p: (-p[0], p[1]))


def write(rd: RunDir) -> None:
    basis, conflicts, assigned = load(rd)
    order = sorted(basis, key=lambda e: (basis[e]["pool"] != "pc", e))
    rows = []
    for email in order:
        b, mine = basis[email], conflicts.get(email, [])
        pubs = b["publications"]
        rows.append([
            email, b["name"], b["pool"], b["orcid"], b["dblp"], b["dblp_name"], b["placed_by"],
            b["basis"], b["years"], len(pubs), sum(bool(p[3]) for p in pubs), "; ".join(b["areas"]),
            len({c["pid"] for c in mine if c["layer"] in COI_LAYERS}),
            *(len({c["pid"] for c in mine if c["layer"] == layer}) for layer in LAYERS),
            len(assigned.get(email, [])),
        ])
    hotcrp.write_csv_atomically(str(rd.reviewers), REVIEWERS_HEADER, rows)
    hotcrp.write_csv_atomically(
        str(rd.publications), PUBLICATIONS_HEADER,
        ([e, basis[e]["name"], basis[e]["pool"], *p] for e in order
         for p in by_newest(basis[e]["publications"])))
    print(f"Records: {len(order)} reviewers -> {rd.reviewers.name}, "
          f"{sum(len(b['publications']) for b in basis.values())} papers -> "
          f"{rd.publications.name}", file=sys.stderr)


def show(rd: RunDir, email: str) -> None:
    basis, conflicts, assigned = load(rd)
    if email not in basis:
        raise KeyError(f"{email} is not a reviewer in {rd.root}")
    b, mine = basis[email], conflicts.get(email, [])
    pubs = b["publications"]
    print(f"{b['name']} <{email}>, {'PC' if b['pool'] == 'pc' else 'extra reviewer'}")
    dblp = f"DBLP {b['dblp']} ({b['dblp_name']})" if b["dblp"] else "no DBLP page"
    print(f"  ORCID {b['orcid'] or '-'}; {dblp}; found by: {b['placed_by']}")
    print(f"  Fingerprint basis: {b['basis']}, {b['years']}-year window, {len(pubs)} paper(s), "
          f"{sum(bool(p[3]) for p in pubs)} with an abstract")
    topics = b["areas"] + [k.strip() for k in b["keywords"].split(",") if k.strip()]
    print(f"  Topics: {'; '.join(topics) or '-'}")
    for year, title, doi, abstract in by_newest(pubs):
        print(f"    {year}  {title}" + (f"  doi:{doi}" if doi else "")
              + ("" if abstract else "  [title only]"))
    coi = sorted((c for c in mine if c["layer"] in COI_LAYERS),
                 key=lambda c: (c["pid"], LAYERS.index(c["layer"])))
    print(f"  Conflicts of interest: {len({c['pid'] for c in coi})} paper(s)")
    for c in coi:
        who = f"{c['author']}: " if c["author"] else ""
        print(f"    #{c['pid']}  {c['layer']}  {who}{c['evidence']}")
    vetoed = {c["pid"] for c in mine if c["layer"] == "topic-veto"}
    if vetoed:
        print(f"  Topic veto (a topic they rated negatively): {len(vetoed)} paper(s)")
    print(f"  Assigned: {' '.join(f'#{pid}' for pid in assigned.get(email, [])) or 'none'}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_dir", help="a run directory, e.g. runs/eurosys27-fall")
    parser.add_argument("--email", action="append", default=[],
                        help="print this reviewer's record instead (repeatable)")
    args = parser.parse_args()
    rd = RunDir(Path(args.run_dir).resolve())
    try:
        if not args.email:
            write(rd)
        for i, email in enumerate(args.email):
            if i:
                print()
            show(rd, email.strip().lower())
    except (FileNotFoundError, KeyError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
