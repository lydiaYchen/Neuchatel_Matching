"""Place the submissions' authors on DBLP by the ORCIDs in the authors CSV.

The co-author conflict layer compares DBLP pages instead of names for an
author identified this way, so a reviewer who wrote with a namesake is not
conflicted. Network (SPARQL); placed ORCIDs are cached and never asked again.
"""

from __future__ import annotations

import json
import sys
from collections import Counter

from . import dblp, hotcrp


def place(authors_csv: str, papers_json: str, cache_path: str) -> None:
    authors = hotcrp.load_author_orcids(authors_csv)
    with open(papers_json, encoding="utf-8") as f:
        people = {(p.get("email") or "").strip().lower()
                  for paper in json.load(f) for p in hotcrp.paper_people(paper)} - {""}
    authors = {e: v for e, v in authors.items() if e in people}
    names: dict[str, str] = {}
    for orcid, name in sorted(authors.values()):
        names.setdefault(orcid, name)

    def on_error(batch: list[str], exc: Exception) -> None:
        print(f"  WARN: DBLP lookup failed for {len(batch)} author ORCID(s), matched by name "
              f"until a rerun: {exc}", file=sys.stderr)

    placed = dblp.place_authors(names, cache_path=cache_path, on_error=on_error)
    pids, why = dblp.author_identities(authors, placed)
    print(f"Paper authors identified on DBLP: {len(pids)} of {len(people)}; by name: "
          + ", ".join(f"{n} {reason}" for reason, n in Counter(why.values()).most_common())
          + f", {len(people - set(authors))} without an ORCID", file=sys.stderr)
