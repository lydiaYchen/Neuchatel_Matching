"""What helps decide on a reserve reviewer: sector, PhD and publication counts.

  sector        academia or industry, from industry keywords in the
                affiliation, then ROR's confident match, then academic
                keywords or an academic email domain.
  PhD           from the education and job titles on their ORCID record.
  publications  the works on their ORCID record, and OpenAlex's works_count
                for their ORCID.

Each response is cached as one JSON file under the `reserve.cache`
directory (`orcid/`, `ror/`, `openalex/`), a 404 as `null`, so a rerun
fetches only what is new. These are hints for a person deciding, never
decisions.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from datetime import date
from pathlib import Path

import requests

USER_AGENT = "eurosys-review-assignment"
FETCH_ATTEMPTS = 6

# Industry keywords are checked before ROR, so a company listed next to a
# university ("Apple Inc. (Ph.D. from Princeton)") and "Samsung Advanced
# Institute of Technology" are industry.
INDUSTRY_RE = re.compile(
    r"\b(inc|ltd|llc|corp|corporation|company|technologies|microsoft|google|meta"
    r"|amazon|aws|nvidia|intel|amd|ibm|huawei|alibaba|tencent|bytedance|tiktok"
    r"|baidu|samsung|xiaomi|ant group|meituan|kuaishou|oracle|apple|cisco|vmware"
    r"|databricks|netflix|uber|ebay|hynix|qualcomm)\b|\bai$",
    re.IGNORECASE,
)
# Academic email domains: .edu, .edu.cn, .ac.uk, .ac.kr, ...
ACADEMIC_EMAIL_RE = re.compile(r"@(?:[\w-]+\.)*(?:edu|ac)(?:\.[a-z]{2})?$", re.IGNORECASE)
# Also matches academic institutions often written as acronyms, e.g. "ETS Montreal".
ACADEMIA_RE = re.compile(
    r"universit|college|institut|academy|school|polytechni|laborator|\blab\b"
    r"|école|ecole|\b(eth|epfl|kaist|cnrs|inria"
    r"|ets|hkust|hku|ntua|bsc|etri|epita|mines|heig-vd|hes-so"
    r"|postech|unist|cmu|uiuc|ucla|kaust|mbzuai|metu|iiit|mit|nus|ntu)\b",
    re.IGNORECASE,
)
# PhD evidence in ORCID education and employment titles.
PHD_RE = re.compile(r"ph\.?\s?d|d\.?\s?phil|doctor", re.IGNORECASE)
PHD_STUDENT_RE = re.compile(r"(ph\.?\s?d|doctoral)\s*(student|candidate)|\bcandidate\b",
                            re.IGNORECASE)
POST_PHD_ROLE_RE = re.compile(
    r"professor|\bprof\b|lecturer|post-?doc|research fellow|research scientist|\breader\b",
    re.IGNORECASE,
)


def fetch_json(url: str, cache_path: Path, params: dict | None = None):
    """GET a JSON URL, caching the result on disk. A 404 is cached as None."""
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    for attempt in range(FETCH_ATTEMPTS):
        wait = 2 ** attempt
        try:
            response = requests.get(url, params=params, headers=headers, timeout=60)
        except (requests.ConnectionError, requests.Timeout):
            if attempt == FETCH_ATTEMPTS - 1:
                raise
        else:
            if response.status_code == 404:
                data = None
                break
            if response.ok:
                data = response.json()
                break
            if attempt == FETCH_ATTEMPTS - 1:
                response.raise_for_status()
            # Rate limited: wait as long as the server asks.
            retry_after = response.headers.get("Retry-After", "")
            if response.status_code == 429 and retry_after.isdigit():
                wait = max(wait, int(retry_after))
        time.sleep(wait)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=cache_path.parent,
                                     suffix=".tmp", delete=False) as f:
        json.dump(data, f)
    os.replace(f.name, cache_path)
    return data


def sector(affiliation: str, email: str, cache: Path) -> str:
    """'academia', 'industry' or 'unknown'."""
    if INDUSTRY_RE.search(affiliation):
        return "industry"
    if not affiliation:
        return "academia" if ACADEMIC_EMAIL_RE.search(email) else "unknown"
    key = hashlib.sha1(affiliation.lower().encode()).hexdigest()
    ror = fetch_json("https://api.ror.org/v2/organizations", cache / "ror" / f"{key}.json",
                     params={"affiliation": affiliation})
    chosen = [item for item in (ror or {}).get("items", []) if item.get("chosen")]
    if chosen:
        return "industry" if "company" in chosen[0]["organization"]["types"] else "academia"
    if ACADEMIA_RE.search(affiliation) or ACADEMIC_EMAIL_RE.search(email):
        return "academia"
    return "unknown"


def orcid_record(orcid: str | None, cache: Path) -> dict | None:
    """The public ORCID record, or None without an ORCID or for a missing record."""
    if not orcid:
        return None
    return fetch_json(f"https://pub.orcid.org/v3.0/{orcid}/record",
                      cache / "orcid" / f"{orcid}.json")


def orcid_publications(record: dict | None) -> int | None:
    """Number of works on the ORCID record; versions of the same work are grouped."""
    if not record:
        return None
    works = (record.get("activities-summary") or {}).get("works") or {}
    return len(works.get("group", []))


def openalex_publications(orcid: str | None, cache: Path) -> int | None:
    """works_count of the OpenAlex author linked to the ORCID, or None."""
    if not orcid:
        return None
    author = fetch_json(f"https://api.openalex.org/authors/orcid:{orcid}",
                        cache / "openalex" / f"{orcid}.json")
    return author.get("works_count") if author else None


def has_phd(record: dict | None) -> str:
    """'yes' for a PhD with a past end date or a post-PhD job title, 'no' for a
    current PhD student, 'unknown' otherwise (a PhD without an end date may be
    finished or not)."""
    if not record:
        return "unknown"
    activities = record.get("activities-summary") or {}

    def summaries(section: str, kind: str) -> list[dict]:
        groups = (activities.get(section) or {}).get("affiliation-group", [])
        return [s[f"{kind}-summary"] for g in groups for s in g.get("summaries", [])]

    degrees = summaries("educations", "education") + summaries("qualifications", "qualification")
    jobs = summaries("employments", "employment")
    in_progress = False
    for degree in degrees:
        title = degree.get("role-title") or ""
        if not PHD_RE.search(title):
            continue
        end_year = ((degree.get("end-date") or {}).get("year") or {}).get("value")
        finished = end_year is not None and int(end_year) <= date.today().year
        if PHD_STUDENT_RE.search(title):
            in_progress = in_progress or not finished
        elif finished:
            return "yes"
        elif end_year:
            in_progress = True
    for job in jobs:
        title = job.get("role-title") or ""
        if PHD_STUDENT_RE.search(title):
            in_progress = in_progress or not job.get("end-date")
        elif POST_PHD_ROLE_RE.search(title):
            return "yes"
    return "no" if in_progress else "unknown"
