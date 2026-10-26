"""The same-country cap: a paper whose authors are mostly from country C holds
at most `cap` reviewers from C.

Country is where the institution is, never anyone's nationality; Hong Kong,
Macao, Taiwan and Singapore are their own codes and never count as China. A
person's country, first source that answers:

  1. their own `country` in HotCRP (the user exports, or for authors without
     an account the authors CSV);
  2. a country or region named in their affiliation ("..., Daejeon, Korea");
  3. their email's country code (.cn, .de; never .com, .edu, .io, ...).

A paper is majority-C when more than half of the authors whose country is
known are in C, and at least half of its authors have a known country; a
paper below that is not capped. The same rule applies to every country.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass

from .. import hotcrp

MAJORITY = 0.5
MIN_RESOLVED = 0.5

# Country and region names as they appear in affiliations. Adjectives
# ("Chinese") are left out on purpose -- "The Chinese University of Hong Kong"
# is not in CN -- and so are names that are also institution words
# ("Georgia", "Jordan").
COUNTRY_NAMES: dict[str, str] = {
    "argentina": "AR", "australia": "AU", "austria": "AT", "bangladesh": "BD",
    "belgium": "BE", "brazil": "BR", "brasil": "BR", "canada": "CA", "chile": "CL",
    "china": "CN", "p r china": "CN", "pr china": "CN", "peoples republic of china": "CN",
    "colombia": "CO", "croatia": "HR", "cyprus": "CY", "czech republic": "CZ", "czechia": "CZ",
    "denmark": "DK", "egypt": "EG", "estonia": "EE", "finland": "FI", "france": "FR",
    "germany": "DE", "deutschland": "DE", "greece": "GR", "hong kong": "HK",
    "hong kong sar": "HK", "hungary": "HU", "iceland": "IS", "india": "IN",
    "indonesia": "ID", "iran": "IR", "ireland": "IE", "israel": "IL", "italy": "IT",
    "italia": "IT", "japan": "JP", "kenya": "KE", "korea": "KR", "south korea": "KR",
    "republic of korea": "KR", "north korea": "KP", "luxembourg": "LU", "macao": "MO",
    "macau": "MO", "malaysia": "MY", "mexico": "MX", "netherlands": "NL",
    "the netherlands": "NL", "holland": "NL", "new zealand": "NZ", "nigeria": "NG",
    "norway": "NO", "pakistan": "PK", "peru": "PE", "philippines": "PH", "poland": "PL",
    "portugal": "PT", "qatar": "QA", "romania": "RO", "russia": "RU",
    "russian federation": "RU", "saudi arabia": "SA", "serbia": "RS", "singapore": "SG",
    "slovakia": "SK", "slovenia": "SI", "south africa": "ZA", "spain": "ES", "espana": "ES",
    "sweden": "SE", "switzerland": "CH", "taiwan": "TW", "thailand": "TH", "tunisia": "TN",
    "ukraine": "UA", "united arab emirates": "AE", "uae": "AE", "united kingdom": "GB",
    "uk": "GB", "great britain": "GB", "england": "GB", "scotland": "GB", "wales": "GB",
    "northern ireland": "GB", "united states": "US", "united states of america": "US",
    "usa": "US", "u s a": "US", "vietnam": "VN", "viet nam": "VN",
}

# Sold to anyone anywhere, so they say nothing about where someone is.
GENERIC_TLDS = frozenset("""
    com edu org net gov mil int info biz name pro app dev xyz online site tech
    io ai co me tv cc ly to gg sh st fm am
""".split())

ISO_ALPHA2: frozenset[str] = frozenset("""
    AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ
    BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR
    CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR
    GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU
    ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ
    LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ
    MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF
    PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI
    SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR
    TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW
""".split())

# A region named beside its sovereign state ("Hong Kong ..., China") is the answer.
_OUTRANKS: dict[str, frozenset[str]] = {
    "HK": frozenset({"CN"}), "MO": frozenset({"CN"}), "TW": frozenset({"CN"}),
}

_PUNCT_RE = re.compile(r"[^\w\s]+", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Casefolded, accent-folded, punctuation-free; a leading "the " dropped."""
    folded = unicodedata.normalize("NFKD", text or "")
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    folded = _WS_RE.sub(" ", _PUNCT_RE.sub(" ", folded.casefold())).strip()
    return folded[4:] if folded.startswith("the ") else folded


def from_hotcrp(raw: str) -> str:
    """An ISO code from a HotCRP `country` cell (a code, or a name such as "USA")."""
    code = (raw or "").strip().upper()
    if code in ISO_ALPHA2:
        return code
    return COUNTRY_NAMES.get(normalize(raw), "")


def from_text(affiliation: str) -> str:
    """The country or region an affiliation names, on whole words; the longest
    name wins, and among equals the rightmost (affiliations end with the place)."""
    tokens = normalize(affiliation).split()
    if not tokens:
        return ""
    matches: dict[str, tuple[int, int]] = {}
    for name, code in COUNTRY_NAMES.items():
        parts = name.split()
        n = len(parts)
        for i in range(len(tokens) - n + 1):
            if tokens[i:i + n] == parts:
                key = (n, i)
                if code not in matches or key > matches[code]:
                    matches[code] = key
    if not matches:
        return ""
    outranked = {c for code in matches for c in _OUTRANKS.get(code, ())}
    live = {code: key for code, key in matches.items() if code not in outranked} or matches
    return max(live, key=lambda code: live[code])


def from_email(email: str) -> str:
    """The country of an address's country-code domain, "" for a generic one."""
    _, _, domain = (email or "").strip().lower().partition("@")
    if not domain:
        return ""
    tld = domain.rsplit(".", 1)[-1]
    if not tld or tld in GENERIC_TLDS or not tld.isalpha() or len(tld) != 2:
        return ""
    code = "GB" if tld == "uk" else tld.upper()
    return code if code in ISO_ALPHA2 else ""


def hotcrp_countries(accounts: hotcrp.Accounts, authors_csv: str) -> dict[str, str]:
    """{email: code} from HotCRP's own `country` fields: the authors CSV, then
    the user exports over it. An author given two countries is left out."""
    seen: dict[str, set[str]] = {}
    with open(authors_csv, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            email = (row.get("email") or "").strip().lower()
            code = from_hotcrp(row.get("country") or "")
            if email and code:
                seen.setdefault(email, set()).add(code)
    out = {e: next(iter(v)) for e, v in seen.items() if len(v) == 1}
    for acct in accounts.accounts:
        code = from_hotcrp(acct.country)
        if code:
            out[acct.email.lower()] = code
    return out


def country(affiliation: str, email: str, declared: str) -> str:
    """The person's country by the module's three sources, "" if none answers."""
    return declared or from_text(affiliation) or from_email(email)


@dataclass(frozen=True)
class CountryCap:
    code: str
    members: frozenset[str]   # candidate reviewers in `code`
    papers: dict[int, int]    # pid -> cap, for the majority-`code` papers


def country_caps(
    papers: list[dict], candidates: list[str], reviewers: Mapping[str, object],
    declared: dict[str, str], cap: int,
) -> tuple[list[CountryCap], dict[str, str], list[int]]:
    """(one cap per country that is some paper's majority, {reviewer: code},
    [pids with too few authors placed to judge])."""
    reviewer_country = {}
    for email in candidates:
        r = reviewers[email]
        reviewer_country[email] = country(r.affiliation, r.email,
                                          declared.get(r.hotcrp_email.lower(), ""))
    majority_of: dict[int, str] = {}
    thin: list[int] = []
    for p in papers:
        authors = p.get("authors") or []
        seen = Counter()
        for a in authors:
            code = country(a.get("affiliation") or "", a.get("email") or "",
                           declared.get((a.get("email") or "").strip().lower(), ""))
            if code:
                seen[code] += 1
        placed = sum(seen.values())
        if not authors:
            continue
        if placed / len(authors) < MIN_RESOLVED:
            thin.append(p["pid"])
            continue
        if placed:
            code, n = seen.most_common(1)[0]
            if n / placed > MAJORITY:
                majority_of[p["pid"]] = code
    caps = []
    for code in sorted(set(majority_of.values())):
        members = frozenset(e for e, c in reviewer_country.items() if c == code)
        caps.append(CountryCap(code, members,
                               {pid: cap for pid, c in majority_of.items() if c == code}))
    return caps, reviewer_country, thin
