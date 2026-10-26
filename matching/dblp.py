"""A reviewer's DBLP publications and co-authors, found from their ORCID.

DBLP is the source of truth for which papers a reviewer wrote (OpenAlex is
asked only for abstracts, by DOI). Everything goes through DBLP's SPARQL
endpoint: dblp.org's own pages answer scripts with a "not a bot" check.

An ORCID is placed on a DBLP person in one of these ways (`via`):

  person        the DBLP person record carries the ORCID (`dblp:orcid`).
  paper         papers carry it on the author entry (`dblp:signatureOrcid`,
                from publisher metadata); the person credited with most of
                them is taken, if the name agrees with HotCRP's.
  orcid-papers  that person is a DBLP catch-all page (`dblp:AmbiguousCreator`:
                every paper of that name DBLP has not assigned to anyone).
                Never taken as the person; only the papers carrying the ORCID
                are theirs, so the list is a lower bound.
  hand          `dblp_placements.csv` names the page (roster.load_placements).
                An account with no ORCID is keyed `dblp:<pid>` instead.

An ORCID DBLP cannot place is `pid: None`: unknown, never guessed by name,
and asked again on the next run. Each paper records whether it is DBLP's
arXiv copy, so the fingerprint can leave out work that only exists as a
preprint, and its co-authors' names and DBLP pages, for the co-author COI.

Results are cached per ORCID (written after every batch, so an interrupted
run resumes) and served from the cache while it covers the window asked for.
"""

from __future__ import annotations

import datetime
import json
import re
import time
from collections.abc import Callable
from pathlib import Path

import requests

from .names import names_agree

ENDPOINT = "https://sparql.dblp.org/sparql"
# Bumped whenever a cache entry's shape changes; older entries are refetched.
CACHE_VERSION = 2
RESOLVE_BATCH = 100   # people per placement query
FETCH_BATCH = 20      # people per publication query
CATCH_ALL_BATCH = 1000
DEFAULT_DELAY = 1.0
MAX_RETRIES = 5
ARXIV_STREAM = "https://dblp.org/streams/journals/corr"
PREFIX = "PREFIX dblp: <https://dblp.org/rdf/schema#>\n"

# How a reviewer is keyed: `Reviewer.pid` is ORCID_PREFIX + the cache key, and
# the cache key is the ORCID, or DBLP_KEY + pid for an account placed by hand
# without one. A DBLP pid has a "/" and no ":", an ORCID neither.
ORCID_PREFIX = "orcid:"
DBLP_KEY = "dblp:"

_PID_RE = re.compile(r"/pid/(\w+/[\w-]+)", re.IGNORECASE)
_BARE_PID_RE = re.compile(r"^(\w+/[\w-]+?)(\.html)?$", re.IGNORECASE)
_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s?#]+", re.IGNORECASE)


def orcid_of(reviewer) -> str | None:
    """The cache key a reviewer's `pid` carries, or None."""
    pid = reviewer.pid or ""
    return pid[len(ORCID_PREFIX):] if pid.startswith(ORCID_PREFIX) else None


def parse_pid(url: str) -> str | None:
    """The DBLP pid of a person link ('https://dblp.org/pid/28/5155.html' -> '28/5155'),
    or of a bare pid; None for anything else."""
    url = (url or "").strip()
    if not url or url.lower() == "none":
        return None
    if "/pid/" not in url.lower():
        bare = _BARE_PID_RE.match(url)
        return bare.group(1) if bare else None
    match = _PID_RE.search(url)
    return match.group(1) if match else None


def normalise_doi(value: str | None) -> str | None:
    """A lowercase bare DOI from a DOI or publisher URL."""
    if not value:
        return None
    match = _DOI_RE.search(value.strip())
    if not match:
        return None
    return match.group(0).rstrip(".>,)").lower()


def query(session: requests.Session, text: str) -> list[dict[str, str]]:
    """Run one SPARQL query; rows as {variable: value}. Retries 429/5xx/drops."""
    for attempt in range(MAX_RETRIES):
        try:
            response = session.post(
                ENDPOINT, data={"query": PREFIX + text},
                headers={"Accept": "application/sparql-results+json"}, timeout=300,
            )
        except (requests.ConnectionError, requests.Timeout):
            response = None
        if response is not None and response.status_code == 200:
            return [{k: v["value"] for k, v in row.items()}
                    for row in response.json()["results"]["bindings"]]
        if response is not None and response.status_code not in (429, 500, 502, 503, 504):
            response.raise_for_status()
        if attempt + 1 < MAX_RETRIES:
            time.sleep(10 * (attempt + 1))
    if response is not None:
        response.raise_for_status()
    raise requests.ConnectionError(f"{ENDPOINT}: no response after {MAX_RETRIES} attempts")


def pid_of(iri: str) -> str:
    """'https://dblp.org/pid/197/6723-1' -> '197/6723-1'."""
    return iri.split("/pid/", 1)[1]


def _orcid_iris(orcids: list[str]) -> str:
    return " ".join(f"<https://orcid.org/{o}>" for o in orcids)


def catch_all_pages(session: requests.Session, pids) -> set[str]:
    """The pids among `pids` that are DBLP catch-all pages."""
    pids = sorted(set(pids))
    found: set[str] = set()
    for i in range(0, len(pids), CATCH_ALL_BATCH):
        people = " ".join(f"<https://dblp.org/pid/{p}>" for p in pids[i:i + CATCH_ALL_BATCH])
        found |= {pid_of(row["p"]) for row in query(session, f"""SELECT ?p WHERE {{
            VALUES ?p {{ {people} }} ?p a dblp:AmbiguousCreator }}""")}
    return found


def _coauthors(others: str, me: str) -> tuple[list[str], dict[str, str]]:
    """A record's co-author names and {pid: name}, from `<IRI>#<name>|...`, without `me`.

    (A FILTER inside the query's OPTIONAL would drop `me` there, but DBLP's
    endpoint then returns no co-authors at all.)
    """
    pairs = [e.split("#", 1) for e in others.split("|") if "#" in e]
    people = {pid_of(iri): name for iri, name in pairs if iri != me and "/pid/" in iri}
    return sorted({name for iri, name in pairs if iri != me}), dict(sorted(people.items()))


def resolve(session: requests.Session, names: dict[str, str]) -> dict[str, dict]:
    """{orcid: {pid, via, dblp_name}} for the ORCIDs DBLP can place (module docstring).

    `names` maps each ORCID to HotCRP's name for it, used only to reject a
    paper-level link to someone else. ORCIDs DBLP cannot place are absent.
    """
    placed: dict[str, dict] = {}
    orcids = sorted(names)
    for i in range(0, len(orcids), RESOLVE_BATCH):
        batch = orcids[i:i + RESOLVE_BATCH]
        for row in query(session, f"""SELECT ?orcid ?p ?name WHERE {{
            VALUES ?orcid {{ {_orcid_iris(batch)} }}
            ?p dblp:orcid ?orcid ; dblp:primaryCreatorName ?name }}"""):
            orcid = row["orcid"].rsplit("/", 1)[-1]
            if orcid not in names:
                continue
            if orcid not in placed or pid_of(row["p"]) < placed[orcid]["pid"]:
                placed[orcid] = {"pid": pid_of(row["p"]), "via": "person", "dblp_name": row["name"]}
        rest = [o for o in batch if o not in placed]
        if not rest:
            continue
        credited: dict[str, list[tuple[int, str, str]]] = {}
        for row in query(session, f"""SELECT ?orcid ?p ?name (COUNT(DISTINCT ?sig) AS ?n) WHERE {{
            VALUES ?orcid {{ {_orcid_iris(rest)} }}
            ?sig dblp:signatureOrcid ?orcid ; dblp:signatureCreator ?p .
            ?p dblp:primaryCreatorName ?name }} GROUP BY ?orcid ?p ?name"""):
            orcid = row["orcid"].rsplit("/", 1)[-1]
            if orcid in rest:
                credited.setdefault(orcid, []).append((int(row["n"]), pid_of(row["p"]), row["name"]))
        ambiguous = catch_all_pages(
            session, {pid for people in credited.values() for _, pid, _ in people})
        for orcid, people in credited.items():
            agreeing = [c for c in people if names_agree(c[2], names[orcid])]
            # Most papers first; ties go to the smaller pid, so reruns agree.
            real = [c for c in agreeing if c[1] not in ambiguous]
            if real:
                n, pid, dblp_name = min(real, key=lambda c: (-c[0], c[1]))
                placed[orcid] = {"pid": pid, "via": "paper", "dblp_name": dblp_name}
            elif agreeing:
                n, pid, dblp_name = min(agreeing, key=lambda c: (-c[0], c[1]))
                placed[orcid] = {"pid": pid, "via": "orcid-papers", "dblp_name": dblp_name}
    return placed


class PlacementError(ValueError):
    """A hand placement names a DBLP page that cannot be the person."""


def place_by_hand(session: requests.Session, pinned: dict[str, str]) -> dict[str, dict]:
    """{key: {pid, via: "hand", dblp_name}} for `pinned` ({key: pid}).

    Raises PlacementError for a pid DBLP does not know or a catch-all page:
    either would fingerprint strangers' papers under the reviewer's name.
    """
    pids = sorted(set(pinned.values()))
    if not pids:
        return {}
    people = " ".join(f"<https://dblp.org/pid/{p}>" for p in pids)
    names = {pid_of(row["p"]): row["name"] for row in query(session, f"""SELECT ?p ?name WHERE {{
        VALUES ?p {{ {people} }} ?p dblp:primaryCreatorName ?name }}""")}
    ambiguous = catch_all_pages(session, pids)
    bad = sorted({o for o, pid in pinned.items() if pid not in names or pid in ambiguous})
    if bad:
        raise PlacementError(
            "placement names a DBLP page that is unknown or a catch-all page: "
            + ", ".join(f"{o} -> {pinned[o]}" for o in bad))
    return {o: {"pid": pid, "via": "hand", "dblp_name": names[pid]} for o, pid in pinned.items()}


def fetch_records(session: requests.Session, pids: list[str], since: int) -> dict[str, list[dict]]:
    """{pid: [{year, title, doi, arxiv, coauthors, coauthor_pids}]} since `since`."""
    records: dict[str, list[dict]] = {pid: [] for pid in pids}
    for i in range(0, len(pids), FETCH_BATCH):
        people = " ".join(f"<https://dblp.org/pid/{p}>" for p in pids[i:i + FETCH_BATCH])
        rows = query(session, f"""SELECT ?p ?pub ?title ?year (SAMPLE(?d) AS ?doi) (SAMPLE(?ax) AS ?arxiv)
            (GROUP_CONCAT(DISTINCT CONCAT(STR(?o), "#", ?other); separator="|") AS ?others) WHERE {{
            VALUES ?p {{ {people} }}
            ?pub dblp:authoredBy ?p ; dblp:title ?title ; dblp:yearOfPublication ?year .
            FILTER(?year >= "{since}"^^<http://www.w3.org/2001/XMLSchema#gYear>)
            OPTIONAL {{ ?pub dblp:doi ?d }}
            OPTIONAL {{ VALUES ?ax {{ <{ARXIV_STREAM}> }} ?pub dblp:publishedInStream ?ax }}
            OPTIONAL {{ ?pub dblp:authoredBy ?o . ?o dblp:primaryCreatorName ?other }}
            }} GROUP BY ?p ?pub ?title ?year""")
        for row in sorted(rows, key=lambda r: (r["p"], r["pub"])):
            names, people_ = _coauthors(row.get("others", ""), row["p"])
            records[pid_of(row["p"])].append({
                "year": int(row["year"][:4]),
                "title": row["title"].strip(),
                "doi": normalise_doi(row.get("doi")) or "",
                "arxiv": bool(row.get("arxiv")),
                "coauthors": names,
                "coauthor_pids": people_,
            })
    return records


def fetch_orcid_papers(session: requests.Session, orcids: list[str], since: int) -> dict[str, list[dict]]:
    """{orcid: [records]}: only the papers whose author entry carries the ORCID
    (for `orcid-papers` placements)."""
    records: dict[str, list[dict]] = {o: [] for o in orcids}
    for i in range(0, len(orcids), FETCH_BATCH):
        rows = query(session, f"""SELECT ?orcid ?me ?pub ?title ?year (SAMPLE(?d) AS ?doi) (SAMPLE(?ax) AS ?arxiv)
            (GROUP_CONCAT(DISTINCT CONCAT(STR(?o), "#", ?other); separator="|") AS ?others) WHERE {{
            VALUES ?orcid {{ {_orcid_iris(orcids[i:i + FETCH_BATCH])} }}
            ?sig dblp:signatureOrcid ?orcid ; dblp:signaturePublication ?pub ; dblp:signatureCreator ?me .
            ?pub dblp:title ?title ; dblp:yearOfPublication ?year .
            FILTER(?year >= "{since}"^^<http://www.w3.org/2001/XMLSchema#gYear>)
            OPTIONAL {{ ?pub dblp:doi ?d }}
            OPTIONAL {{ VALUES ?ax {{ <{ARXIV_STREAM}> }} ?pub dblp:publishedInStream ?ax }}
            OPTIONAL {{ ?pub dblp:authoredBy ?o . ?o dblp:primaryCreatorName ?other }}
            }} GROUP BY ?orcid ?me ?pub ?title ?year""")
        for row in sorted(rows, key=lambda r: (r["orcid"], r["pub"])):
            orcid = row["orcid"].rsplit("/", 1)[-1]
            if orcid not in records:
                continue
            names, people = _coauthors(row.get("others", ""), row["me"])
            records[orcid].append({
                "year": int(row["year"][:4]),
                "title": row["title"].strip(),
                "doi": normalise_doi(row.get("doi")) or "",
                "arxiv": bool(row.get("arxiv")),
                "coauthors": names,
                "coauthor_pids": people,
            })
    return records


def title_key(title: str) -> str:
    """How one paper listed twice (preprint and published) is recognised."""
    return title.lower().rstrip(". ")


def to_publications(
    records: list[dict], since: int, *, exclude_arxiv: bool = False,
) -> list[tuple[int, str, list[str]]]:
    """Cache records -> `(year, title, dois)`, newest first, one per title.

    Copies of one paper (same `title_key`) count once, newest kept, before the
    window is applied. `dois` gathers every copy's DOI: a USENIX paper has
    none, but its arXiv copy's DOI can still find it an abstract.
    `exclude_arxiv` leaves out a paper whose every copy is on arXiv, and dates
    and titles the rest by their newest non-arXiv copy.
    """
    copies: dict[str, list[dict]] = {}
    for rec in sorted(records, key=lambda r: (-r["year"], r["title"].lower())):
        copies.setdefault(title_key(rec["title"]), []).append(rec)
    pubs = []
    for group in copies.values():
        counted = [r for r in group if not r["arxiv"]] if exclude_arxiv else group
        if counted and counted[0]["year"] >= since:
            dois = list(dict.fromkeys(r["doi"] for r in group if r["doi"]))
            pubs.append((counted[0]["year"], counted[0]["title"], dois))
    return sorted(pubs, key=lambda p: (-p[0], p[1].lower()))


def fallback_publications(records: list[dict], since: int, limit: int) -> list[tuple[int, str, list[str]]]:
    """The `limit` most recent papers since `since`, arXiv preprints included."""
    return to_publications(records, since)[:limit]


def load_cache(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    with p.open(encoding="utf-8") as f:
        return json.load(f)


def save_cache(cache: dict, path: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)
    tmp.replace(p)


def since_year(years: int, today: datetime.date | None = None) -> int:
    """First year of a `years`-long window ending this year."""
    return (today or datetime.date.today()).year - years + 1


def fetch_for_orcids(
    names: dict[str, str],
    *,
    years: int,
    fetch_years: int,
    cache_path: str,
    placements: dict[str, str],
    session: requests.Session | None = None,
    delay: float = DEFAULT_DELAY,
    on_error: Callable[[list[str], Exception], None] | None = None,
) -> dict[str, dict]:
    """Cache entries for every key in `names` ({key: HotCRP name}), cache first.

    `fetch_years`, when wider than `years`, widens what is fetched (for the
    co-author window and the fallback papers). `placements` ({ORCID: pid})
    places those by hand, and a `dblp:<pid>` key is placed on its pid; an
    entry placed otherwise than the placements now say is refetched, as is an
    unplaced one. A failed batch is left out and reported through `on_error`;
    PlacementError is raised, since a bad placement is fixed in the file.
    """
    session = session or requests.Session()
    cache = load_cache(cache_path)
    placements = {**{o: o[len(DBLP_KEY):] for o in names if o.startswith(DBLP_KEY)},
                  **placements}
    fetch_since = since_year(max(years, fetch_years))

    def as_placed(o: str) -> bool:
        if o in placements:
            return cache[o].get("via") == "hand" and cache[o]["pid"] == placements[o]
        return cache[o].get("via") != "hand"

    fresh = {
        o for o in names
        if cache.get(o, {}).get("version") == CACHE_VERSION
        and cache[o].get("pid")
        and cache[o].get("since", fetch_since + 1) <= fetch_since
        and all("arxiv" in r for r in cache[o]["records"])
        and "catch_all_coauthors" in cache[o]
        and as_placed(o)
    }
    todo = sorted(set(names) - fresh)
    for i in range(0, len(todo), RESOLVE_BATCH):
        batch = todo[i:i + RESOLVE_BATCH]
        try:
            pinned = {o: placements[o] for o in batch if o in placements}
            placed = resolve(session, {o: names[o] for o in batch if o not in pinned})
            placed.update(place_by_hand(session, pinned))
            records = fetch_records(
                session, sorted({v["pid"] for v in placed.values() if v["via"] != "orcid-papers"}),
                fetch_since,
            )
            by_orcid = fetch_orcid_papers(
                session, sorted(o for o, v in placed.items() if v["via"] == "orcid-papers"), fetch_since,
            )
            catch_all = catch_all_pages(session, {
                pid for recs in (*records.values(), *by_orcid.values())
                for r in recs for pid in r["coauthor_pids"]
            })
        except PlacementError:
            raise
        except Exception as exc:  # noqa: BLE001 -- reported; the batch is retried next run
            if on_error:
                on_error(batch, exc)
            continue
        today = datetime.date.today().isoformat()
        for orcid in batch:
            where = placed.get(orcid, {})
            recs = (by_orcid[orcid] if where.get("via") == "orcid-papers"
                    else records.get(where["pid"], [])) if where else []
            cache[orcid] = {
                "version": CACHE_VERSION,
                "pid": where.get("pid"),
                "via": where.get("via"),
                "dblp_name": where.get("dblp_name"),
                "since": fetch_since,
                "fetched_at": today,
                "records": recs,
                "catch_all_coauthors": sorted(
                    {pid for r in recs for pid in r["coauthor_pids"]} & catch_all),
            }
            fresh.add(orcid)
        save_cache(cache, cache_path)
        if delay and i + RESOLVE_BATCH < len(todo):
            time.sleep(delay)
    return {o: cache[o] for o in names if o in fresh}


def load_coauthors(path: str, *, years: int) -> tuple[dict[str, dict[str, list]], list[str]]:
    """({reviewer pid: {co-author name: [[year, title], ...]}}, too_narrow) from the cache.

    An unplaced ORCID is left out, so the co-author index counts that reviewer
    as uncovered, never as having no co-authors. `too_narrow` lists pids whose
    cached window starts after the one asked for.
    """
    with open(path, encoding="utf-8") as f:
        cache = json.load(f)
    since = since_year(years)
    coauthors: dict[str, dict[str, list]] = {}
    too_narrow: list[str] = []
    for orcid, entry in sorted(cache.items()):
        if entry.get("version") != CACHE_VERSION or not entry.get("pid"):
            continue
        if entry.get("since", since + 1) > since:
            too_narrow.append(ORCID_PREFIX + orcid)
            continue
        coauthors[ORCID_PREFIX + orcid] = _shared(entry, since, lambda rec: rec["coauthors"])
    return coauthors, too_narrow


def load_coauthor_ids(path: str, *, years: int) -> tuple[dict[str, dict[str, list]], dict[str, str]]:
    """`load_coauthors` by DBLP page instead of name, plus {catch-all page: its name}."""
    with open(path, encoding="utf-8") as f:
        cache = json.load(f)
    since = since_year(years)
    ids: dict[str, dict[str, list]] = {}
    catch_all: dict[str, str] = {}
    for orcid, entry in sorted(cache.items()):
        if (entry.get("version") != CACHE_VERSION or not entry.get("pid")
                or entry.get("since", since + 1) > since or "catch_all_coauthors" not in entry):
            continue
        ids[ORCID_PREFIX + orcid] = _shared(entry, since, lambda rec: rec["coauthor_pids"])
        pages = set(entry["catch_all_coauthors"])
        for rec in entry["records"]:
            catch_all.update((p, n) for p, n in rec["coauthor_pids"].items() if p in pages)
    return ids, catch_all


def _shared(entry: dict, since: int, coauthors_of) -> dict[str, list]:
    """{co-author: [[year, title], ...]} since `since`, copies of one paper counted once."""
    shared: dict[str, list] = {}
    credited: set[tuple[str, str]] = set()
    for rec in sorted(entry.get("records", []), key=lambda r: (-r["year"], r["title"].lower())):
        if rec["year"] < since:
            continue
        key = title_key(rec["title"])
        for who in coauthors_of(rec):
            if (key, who) not in credited:
                credited.add((key, who))
                shared.setdefault(who, []).append([rec["year"], rec["title"]])
    return shared


def place_authors(
    names: dict[str, str],
    *,
    cache_path: str,
    session: requests.Session | None = None,
    delay: float = DEFAULT_DELAY,
    on_error: Callable[[list[str], Exception], None] | None = None,
) -> dict[str, dict]:
    """{orcid: {pid, via, dblp_name}} for paper authors ({orcid: HotCRP name}),
    cache first; an unplaced ORCID is asked again."""
    session = session or requests.Session()
    cache = load_cache(cache_path)
    todo = sorted(o for o in names
                  if cache.get(o, {}).get("version") != CACHE_VERSION or not cache[o].get("pid"))
    done = set(names) - set(todo)
    for i in range(0, len(todo), RESOLVE_BATCH):
        batch = todo[i:i + RESOLVE_BATCH]
        try:
            placed = resolve(session, {o: names[o] for o in batch})
        except Exception as exc:  # noqa: BLE001 -- reported; the author falls back to names
            if on_error:
                on_error(batch, exc)
            continue
        today = datetime.date.today().isoformat()
        for orcid in batch:
            where = placed.get(orcid, {})
            cache[orcid] = {"version": CACHE_VERSION, "pid": where.get("pid"),
                            "via": where.get("via"), "dblp_name": where.get("dblp_name"),
                            "fetched_at": today}
            done.add(orcid)
        save_cache(cache, cache_path)
        if delay and i + RESOLVE_BATCH < len(todo):
            time.sleep(delay)
    return {o: cache[o] for o in names if o in done}


def author_identities(
    authors: dict[str, tuple[str, str]], cache: dict,
) -> tuple[dict[str, str], dict[str, str]]:
    """({author email: DBLP pid}, {author email: why not}) for the co-author COI.

    An author is identified only on a person's own page whose name agrees
    with the one they gave HotCRP; the reasons for the rest are `not on DBLP`,
    `catch-all page`, `name disagrees` and `not looked up`.
    """
    pids: dict[str, str] = {}
    why: dict[str, str] = {}
    for email, (orcid, name) in sorted(authors.items()):
        entry = cache.get(orcid)
        if entry is None or entry.get("version") != CACHE_VERSION:
            why[email] = "not looked up"
        elif not entry.get("pid"):
            why[email] = "not on DBLP"
        elif entry.get("via") == "orcid-papers":
            why[email] = "catch-all page"
        elif not names_agree(entry.get("dblp_name") or "", name):
            why[email] = "name disagrees"
        else:
            pids[email] = entry["pid"]
    return pids, why
