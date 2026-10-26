"""Abstracts of DBLP papers, looked up on OpenAlex by DOI.

OpenAlex only describes the papers DBLP says a reviewer wrote; it never
decides which papers those are (its author profiles both split people and
merge namesakes). A paper without an abstract is embedded from its title.

Set OPENALEX_API_KEY (a free account's key) in the environment or `.env`:
without it OpenAlex allows about 1,000 requests a day. Only DOIs go over the
wire.
"""

from __future__ import annotations

import json
import os
import random
import sys
import time
from collections.abc import Callable
from pathlib import Path

import requests

from .dblp import normalise_doi

API = "https://api.openalex.org"
SOURCE = "openalex"
DOI_BATCH = 50          # OpenAlex ORs up to 50 values in one filter
DEFAULT_DELAY = 0.1
MAX_RETRIES = 5
MAX_BACKOFF = 300.0


def reassemble_abstract(inverted: dict[str, list[int]] | None) -> str:
    """Rebuild an abstract from OpenAlex's {word: [positions]} inverted index."""
    if not inverted:
        return ""
    positions: list[tuple[int, str]] = []
    for word, at in inverted.items():
        positions.extend((i, word) for i in at)
    positions.sort()
    return " ".join(word for _, word in positions).strip()


def load_cache(path: str) -> dict:
    """{doi: abstract}, "" meaning OpenAlex has none; {} when absent."""
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


def get_with_retry(session: requests.Session, url: str, params: dict,
                   headers: dict | None) -> requests.Response:
    """GET, waiting out a 429/5xx (honouring Retry-After up to MAX_BACKOFF) or a dropped connection."""
    for attempt in range(MAX_RETRIES):
        try:
            resp = session.get(url, params=params, timeout=60, headers=headers)
        except (requests.ConnectionError, requests.Timeout):
            if attempt == MAX_RETRIES - 1:
                raise
            time.sleep(min(2 ** attempt * random.uniform(0.75, 1.25), MAX_BACKOFF))
            continue
        if resp.status_code == 429 or resp.status_code in (500, 502, 503, 504):
            if attempt == MAX_RETRIES - 1:
                break
            retry_after = resp.headers.get("Retry-After")
            wait = (min(int(retry_after), MAX_BACKOFF) if retry_after and retry_after.isdigit()
                    else min(2 ** attempt * random.uniform(0.75, 1.25), MAX_BACKOFF))
            print(f"    OpenAlex {resp.status_code}; waiting {wait:.0f}s", file=sys.stderr)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp
    resp.raise_for_status()
    return resp


def fetch_abstracts(
    dois: list[str],
    *,
    cache_path: str,
    session: requests.Session | None = None,
    delay: float = DEFAULT_DELAY,
    on_error: Callable[[list[str], Exception], None] | None = None,
) -> dict[str, str]:
    """{doi: abstract} for every DOI in `dois` that is cached or fetched.

    "" means OpenAlex has no abstract (or no such work) and is not asked
    again. A failed batch is left out, reported through `on_error`, and
    retried next run. A DOI holding `,` or `|` cannot go in an OpenAlex filter
    and counts as having none.
    """
    session = session or requests.Session()
    api_key = os.environ.get("OPENALEX_API_KEY", "").strip()
    mailto = os.environ.get("OPENALEX_MAILTO", "").strip()
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
    cache = load_cache(cache_path)
    wanted = list(dict.fromkeys(d for d in dois if d))
    todo = [d for d in wanted if d not in cache]
    for d in [d for d in todo if "," in d or "|" in d]:
        cache[d] = ""
    todo = [d for d in todo if d not in cache]
    for i in range(0, len(todo), DOI_BATCH):
        batch = todo[i:i + DOI_BATCH]
        params = {"filter": "doi:" + "|".join(batch), "select": "doi,abstract_inverted_index",
                  "per-page": 200}
        if mailto:
            params["mailto"] = mailto
        try:
            response = get_with_retry(session, f"{API}/works", params, headers)
        except Exception as exc:  # noqa: BLE001 -- reported; the batch is retried next run
            if on_error:
                on_error(batch, exc)
            continue
        found: dict[str, str] = {}
        for work in response.json().get("results", []):
            doi = normalise_doi(work.get("doi"))
            # OpenAlex occasionally holds two works under one DOI; keep any abstract.
            if doi and not found.get(doi):
                found[doi] = reassemble_abstract(work.get("abstract_inverted_index"))
        for d in batch:
            cache[d] = found.get(d, "")
        save_cache(cache, cache_path)
        if delay:
            time.sleep(delay)
    return {d: cache[d] for d in wanted if d in cache}
