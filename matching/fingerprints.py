"""Reviewer and paper fingerprints (stage 2 of the pipeline).

A reviewer's fingerprint pools one SPECTER2 document per DBLP paper of the
last `years` calendar years (title and OpenAlex abstract), plus one document
of their HotCRP topics, weighted as one paper. Papers DBLP lists only on
arXiv are left out (an arXiv copy still lends its DOI for the abstract).
What the fingerprint rests on when that is empty, in order:

  * `fallback_papers`: their N most recent papers of the last
    `fallback_years`, arXiv included (senior and industry reviewers);
  * their topics alone (no ORCID, or one DBLP cannot place);
  * no topics either: the HotCRP topics of the submissions they authored.

`fingerprint_overrides.csv` (`email,source,years,note`) settles individual
reviewers by hand: `own-papers` embeds the abstracts of their own submissions
instead of DBLP, `area-only` ignores DBLP (a page that mixes two people), and
`dblp` with `years` reads their page over a wider window.

A paper's fingerprint pools its title+abstract and its topics, equally.

Fingerprints are cached by a content key (who, which papers, the model), so
an unchanged reviewer or paper is never embedded twice. Each run also writes
what every reviewer's fingerprint was built from (`fingerprint_basis`), which
`records.py` turns into the publication record.
"""

from __future__ import annotations

import csv
import dataclasses
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

from . import dblp, embedding, openalex
from .dblp import DBLP_KEY, ORCID_PREFIX, orcid_of
from .roster import Reviewer

# Part of every content key: changing any of them re-embeds everyone.
SCHEMA_VERSION = 3
PAPER_SCHEMA_VERSION = 2
AREA_WEIGHT = 1.0
SOURCES = ("own-papers", "area-only", "dblp")


def load_overrides(path: str) -> dict[str, tuple[str, int | None]]:
    """{email: (source, years or None)} from `fingerprint_overrides.csv`; {} if absent."""
    try:
        f = open(path, newline="", encoding="utf-8")
    except FileNotFoundError:
        return {}
    overrides: dict[str, tuple[str, int | None]] = {}
    with f:
        for line, row in enumerate(csv.DictReader(f), 2):
            email = (row.get("email") or "").strip().lower()
            source = (row.get("source") or "").strip().lower()
            years = (row.get("years") or "").strip()
            if not email and not source:
                continue
            if source not in SOURCES:
                raise ValueError(f"{path}:{line}: source must be one of {SOURCES}, got {source!r}")
            if (source == "dblp") != bool(years) or (years and not years.isdigit()):
                raise ValueError(f"{path}:{line}: a years value (a whole number) goes with "
                                 f"source dblp, and only with it")
            overrides[email] = (source, int(years) if years else None)
    return overrides


def own_submissions(papers: list[dict], emails: set[str]) -> dict[str, list[tuple]]:
    """{email: [(year, title, "", abstract, "own-submission")]} for their own submissions."""
    out: dict[str, list[tuple]] = {}
    for paper in papers:
        abstract = (paper.get("abstract") or "").strip()
        if not abstract:
            continue
        year = datetime.fromtimestamp(paper["submitted_at"], tz=timezone.utc).year
        for author in paper.get("authors") or []:
            email = (author.get("email") or "").strip().lower()
            if email in emails:
                out.setdefault(email, []).append(
                    (year, paper.get("title") or "", "", abstract, "own-submission"))
    return out


def submission_topics(reviewers: list[Reviewer], nothing: set[str], papers: list[dict]):
    """Reviewers in `nothing` (no paper, no topic) take the topics of the
    submissions they authored, most frequent first, ties alphabetically.
    Returns the roster with them filled in, and {email: topics}."""
    authored: dict[str, Counter] = defaultdict(Counter)
    for paper in papers:
        topics = paper.get("topics") or []
        for author in paper.get("authors") or [] if topics else []:
            email = (author.get("email") or "").strip().lower()
            if email:
                authored[email].update(topics)
    out, filled = [], {}
    for r in reviewers:
        counts = authored.get(r.email)
        if counts and r.email in nothing:
            ranked = [t for t, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
            areas = ranked[:3] + [""] * (3 - len(ranked[:3]))
            r = dataclasses.replace(r, primary=areas[0], secondary=areas[1], tertiary=areas[2],
                                    keywords=", ".join(ranked[3:]))
            filled[r.email] = ranked
        out.append(r)
    return out, filled


def fingerprint_key(r: Reviewer, publications: list[tuple], years: int, source: str) -> str:
    """The content key of a reviewer fingerprint."""
    payload = {
        "schema_version": SCHEMA_VERSION,
        "model": embedding.BASE_MODEL,
        "adapter": embedding.PROXIMITY_ADAPTER,
        "email": r.email,
        "pid": r.pid,
        "primary": r.primary,
        "secondary": r.secondary,
        "tertiary": r.tertiary,
        "keywords": r.keywords,
        "years": years,
        "max_titles": None,
        "area_weight": AREA_WEIGHT,
        "publications": publications,
    }
    if source != "dblp":
        payload["fingerprint_source"] = source
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def placement_of(r: Reviewer, entries: dict[str, dict], failed: set[str]) -> tuple[str, str, str]:
    """(DBLP pid, DBLP name, how it was found) for one reviewer.

    How: the cache entry's `via` (person, paper, orcid-papers, hand), or `not
    found` (DBLP cannot place the ORCID), `lookup failed`, `no ORCID`.
    """
    key = orcid_of(r)
    if key is None:
        return "", "", "no ORCID"
    if r.pid in failed:
        return "", "", "lookup failed"
    entry = entries.get(key) or {}
    if not entry.get("pid"):
        return "", "", "not found"
    return entry["pid"], entry.get("dblp_name", ""), entry.get("via", "")


def basis_of(r: Reviewer, entry: dict | None, key: str, publications: list[tuple], *,
             override: str | None, fell_back: bool, from_submissions: bool) -> str:
    """What a reviewer's fingerprint rests on (README.md lists the values)."""
    if entry is None:
        return "none"
    if entry.get("fingerprint_key") != key:
        return "stale"
    if override == "area-only":
        return "topics-override"
    if override == "own-papers" and publications:
        return "own-submissions"
    if publications:
        return "dblp-fallback" if fell_back else "dblp"
    return "submission-topics" if from_submissions else "topics"


def build_reviewers(
    reviewers: list[Reviewer],
    *,
    papers_path: str,
    cache_path: str,
    basis_path: str,
    dblp_cache: str,
    abstract_cache: str,
    placements: dict[str, str],
    overrides: dict[str, tuple[str, int | None]],
    years: int,
    fetch_years: int,
    fallback_papers: int,
    fallback_years: int,
    basis_only: bool = False,
) -> None:
    """Fingerprint one pool into `cache_path` and write its basis to `basis_path`.

    `basis_only` embeds nothing and leaves `cache_path` alone: it records what
    the cached fingerprints were built from, reading the DBLP and OpenAlex
    caches as they stand without asking either service anything.
    """
    with open(papers_path, encoding="utf-8") as f:
        all_papers = json.load(f)
    with_orcid = [r for r in reviewers if orcid_of(r)]
    years_of = {orcid_of(r): overrides[r.email][1] for r in with_orcid
                if r.email in overrides and overrides[r.email][1]}
    names: dict[str, str] = {}
    for r in with_orcid:
        names.setdefault(orcid_of(r), r.name)
    pinned = {orcid_of(r): placements[r.email] for r in with_orcid
              if r.email in placements and not orcid_of(r).startswith(DBLP_KEY)}
    failed: set[str] = set()

    def on_dblp_error(keys: list[str], exc: Exception) -> None:
        failed.update(ORCID_PREFIX + k for k in keys)
        print(f"  WARN: DBLP lookup failed for {len(keys)} reviewer(s): {exc}", file=sys.stderr)

    if basis_only:
        cached = dblp.load_cache(dblp_cache)
        entries = {o: cached[o] for o in names if o in cached}
        if len(entries) < len(names):
            on_dblp_error(sorted(set(names) - set(entries)), LookupError(f"not in {dblp_cache}"))
    else:
        # One fetch per window, so a reviewer with a wider window does not
        # widen (and refetch) everyone's.
        entries = {}
        for window in sorted({years, *years_of.values()}):
            group = {o: n for o, n in names.items() if years_of.get(o, years) == window}
            if group:
                entries.update(dblp.fetch_for_orcids(
                    group, years=window, fetch_years=fetch_years, cache_path=dblp_cache,
                    placements=pinned, on_error=on_dblp_error))

    def since_of(o: str) -> int:
        return dblp.since_year(years_of.get(o, years))

    papers_by_orcid = {o: dblp.to_publications(e["records"], since_of(o), exclude_arxiv=True)
                       for o, e in entries.items()}
    fell_back = sorted(o for o, e in entries.items()
                       if fallback_papers and e["pid"] and not papers_by_orcid[o])
    fallback_since = dblp.since_year(fallback_years)
    for o in fell_back:
        papers_by_orcid[o] = dblp.fallback_publications(entries[o]["records"], fallback_since,
                                                        fallback_papers)
    wanted_dois = [d for pubs in papers_by_orcid.values() for _, _, dois in pubs for d in dois]
    if basis_only:
        cached = openalex.load_cache(abstract_cache)
        abstracts = {d: cached[d] for d in wanted_dois if d in cached}
    else:
        abstracts = openalex.fetch_abstracts(
            wanted_dois, cache_path=abstract_cache,
            on_error=lambda dois, exc: print(f"  WARN: OpenAlex lookup failed for {len(dois)} "
                                             f"DOI(s): {exc}", file=sys.stderr))
    by_orcid: dict[str, list[tuple]] = {}
    for orcid, pubs in papers_by_orcid.items():
        by_orcid[orcid] = []
        for year, title, dois in pubs:
            abstract = next((abstracts[d] for d in dois if abstracts.get(d)), "")
            by_orcid[orcid].append(
                (year, title, dois[0] if dois else "", abstract, openalex.SOURCE if abstract else ""))

    source_of = {r.email: overrides.get(r.email, ("dblp", None))[0] for r in reviewers}
    own = own_submissions(all_papers, {e for e, s in source_of.items() if s == "own-papers"})
    publications: dict[str, list[tuple]] = {}
    for r in reviewers:
        if source_of[r.email] == "own-papers":
            publications[r.email] = list(own.get(r.email, []))
        elif orcid_of(r) and source_of[r.email] != "area-only":
            publications[r.email] = list(by_orcid.get(orcid_of(r), []))
        else:
            publications[r.email] = []
    nothing = {r.email for r in reviewers
               if not (publications[r.email] or r.primary or r.secondary or r.tertiary or r.keywords)}
    reviewers, from_submissions = submission_topics(reviewers, nothing, all_papers)

    placed = Counter(e["via"] for e in entries.values() if e["pid"])
    print(f"DBLP by ORCID: {len(names)} looked up, {sum(placed.values())} placed ("
          + ", ".join(f"{n} {via}" for via, n in sorted(placed.items())) + "); "
          f"{sum(len(p) for p in by_orcid.values())} papers, "
          f"{sum(bool(p[3]) for pubs in by_orcid.values() for p in pubs)} with an abstract; "
          f"{len(fell_back)} on the fallback papers; {len(from_submissions)} on their "
          f"submissions' topics", file=sys.stderr)

    window_of = {r.email: overrides[r.email][1] if r.email in overrides and overrides[r.email][1]
                 else years for r in reviewers}
    keys = {r.email: fingerprint_key(r, publications[r.email], window_of[r.email], source_of[r.email])
            for r in reviewers}
    cache = embedding.load_json(cache_path)
    if not basis_only:
        pending = []
        for r in reviewers:
            entry = cache.get(r.email)
            if r.pid in failed and entry is not None:
                entry["dblp_fetch_complete"] = False   # keep the old vector, retry next run
                continue
            if (entry is None or entry.get("fingerprint_key") != keys[r.email]
                    or not entry.get("dblp_fetch_complete", True)):
                pending.append(r)
        print(f"{len(reviewers)} reviewers; {len(pending)} to embed", file=sys.stderr)
        docs, embed = [], []
        for r in pending:
            if not (publications[r.email] or r.primary or r.secondary or r.tertiary or r.keywords):
                cache.pop(r.email, None)       # nothing to embed: no fingerprint, never assigned
                print(f"WARNING: {r.email} has no papers and no topics and cannot be assigned",
                      file=sys.stderr)
                continue
            embed.append(r)
        if embed:
            encoder = embedding.Encoder()
            for r in embed:
                group = [(encoder.doc(title, abstract), 1.0)
                         for _, title, _, abstract, _ in publications[r.email]]
                if r.primary or r.secondary or r.tertiary or r.keywords:
                    areas = ", ".join(a for a in (r.primary, r.secondary, r.tertiary) if a)
                    group.append((encoder.doc(areas, r.keywords), AREA_WEIGHT))
                docs.append(group)
            for r, vector in zip(embed, encoder.fingerprints(docs)):
                cache[r.email] = {
                    "vector": vector.tolist(),
                    "fingerprint_key": keys[r.email],
                    "dblp_fetch_complete": r.pid not in failed,
                }
        if pending or failed:
            embedding.save_json(cache, cache_path)

    basis = {}
    for r in reviewers:
        pid, dblp_name, placed_by = placement_of(r, entries, failed)
        kind = basis_of(r, cache.get(r.email), keys[r.email], publications[r.email],
                        override=overrides.get(r.email, (None,))[0],
                        fell_back=orcid_of(r) in fell_back, from_submissions=r.email in from_submissions)
        basis[r.email] = {
            "name": r.name, "pool": r.pool, "orcid": orcid_of(r) or "", "dblp": pid,
            "dblp_name": dblp_name, "placed_by": placed_by, "basis": kind,
            "years": window_of[r.email],
            "areas": [a for a in (r.primary, r.secondary, r.tertiary) if a],
            "keywords": r.keywords,
            "publications": [] if kind == "stale" else
            [[year, title, doi, source if abstract else ""]
             for year, title, doi, abstract, source in publications[r.email]],
        }
    embedding.save_json(basis, basis_path)
    counts = Counter(b["basis"] for b in basis.values())
    print(f"Fingerprint basis: {dict(sorted(counts.items()))}", file=sys.stderr)
    if counts["stale"]:
        print(f"WARNING: {counts['stale']} cached fingerprint(s) were not built from these "
              f"inputs; rebuild the fingerprints (run without --skip-fingerprints)",
              file=sys.stderr)


def _paper_key(paper: dict) -> str:
    """The content key of a paper fingerprint: title, abstract, topics, model."""
    parts = [
        str(PAPER_SCHEMA_VERSION), embedding.BASE_MODEL, embedding.PROXIMITY_ADAPTER,
        repr(float(AREA_WEIGHT)), paper.get("title") or "", paper.get("abstract") or "",
    ] + list(paper.get("topics", []))
    return hashlib.sha1("\x00".join(parts).encode("utf-8")).hexdigest()


def build_papers(papers: list[dict], cache_path: str) -> dict:
    """Fingerprint any paper missing or changed in the cache; returns the cache."""
    cache = embedding.load_json(cache_path)
    pending = [p for p in papers if cache.get(str(p["pid"]), {}).get("doc_key") != _paper_key(p)]
    if pending:
        encoder = embedding.Encoder()
        docs = [[(encoder.doc(p["title"], p.get("abstract", "")), 1.0),
                 (encoder.doc(", ".join(p.get("topics", []))), AREA_WEIGHT)] for p in pending]
        for p, vector in zip(pending, encoder.fingerprints(docs)):
            cache[str(p["pid"])] = {"vector": vector.tolist(), "doc_key": _paper_key(p)}
        embedding.save_json(cache, cache_path)
    return cache
