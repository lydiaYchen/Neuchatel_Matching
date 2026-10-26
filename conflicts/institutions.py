"""Telling whether a reviewer works where an author does.

Two signals, either enough:

  affiliation   one of the reviewer's search terms appears as whole words in
                an author's affiliation, both normalised. The terms are the
                reviewer's own affiliation, plus known abbreviations of it
                (ABBREVIATIONS); `affiliation_terms.csv` replaces them for an
                affiliation that needs more, such as a combined one ("NVIDIA &
                Stanford University") or a company whose papers name a
                subsidiary.
  email domain  the institutional part of the reviewer's email domain is one
                of the authors'. Webmail and alumni domains match nothing.
"""

from __future__ import annotations

import csv
import os
import re
import unicodedata

# Abbreviations authors use for an institution, keyed by the normalised full name.
ABBREVIATIONS = {
    "beihang university": ["buaa"],
    "beijing university of posts and telecommunications": ["bupt"],
    "carnegie mellon university": ["cmu"],
    "chinese university of hong kong": ["cuhk"],
    "east china normal university": ["ecnu"],
    "hong kong polytechnic university": ["polyu"],
    "hong kong university of science and technology": ["hkust"],
    "hong kong university of science and technology guangzhou": [
        "hong kong university of science and technology", "hkust"],
    "huazhong university of science and technology": ["hust"],
    "korea advanced institute of science and technology": ["kaist"],
    "national university of defense technology": ["nudt"],
    "national university of singapore": ["nus"],
    "pohang university of science and technology": ["postech"],
    "postech": ["pohang university of science and technology"],
    "shanghai jiaotong university": ["sjtu"],
    "southern university of science and technology": ["sustech"],
    "technical university of darmstadt": ["tu darmstadt"],
    "technical university of munich": ["tum", "technische universitat munchen"],
    "tsinghua university": ["tsinghua"],
    "university of california berkeley": ["uc berkeley"],
    "university of california los angeles": ["ucla"],
    "university of california san diego": ["ucsd", "uc san diego"],
    "university of electronic science and technology of china": ["uestc"],
    "university of illinois urbana champaign": [
        "uiuc", "university of illinois at urbana champaign"],
    "university of science and technology of china": ["ustc"],
    "university of texas at austin": ["ut austin"],
    "university of wisconsin madison": ["uw madison"],
}
# Words that, in front of a term, make it a different institution
# ("chinese university of hong kong" is not "university of hong kong").
NOT_PRECEDED_BY = {
    "university of hong kong": ["chinese", "city", "education", "open", "metropolitan"],
    "york university": ["new"],
}
# Shared mail providers and alumni/society addresses say nothing about the
# current institution.
PUBLIC_EMAIL_DOMAINS = {
    "126.com", "163.com", "acm.org", "aliyun.com", "foxmail.com", "gmail.com", "googlemail.com",
    "hotmail.com", "ieee.org", "tsinghua.org.cn", "icloud.com", "live.com", "mail.com", "me.com",
    "outlook.com", "proton.me", "protonmail.com", "qq.com", "sina.com", "yahoo.com", "yeah.net",
}
# Second-level labels under which the institution is the third label
# (ict.ac.cn, cam.ac.uk, sjtu.edu.cn).
GENERIC_SLDS = {"ac", "co", "com", "edu", "gov", "net", "org"}


def normalize(text: str) -> str:
    """Lowercase, strip accents and punctuation, and drop a leading 'the'."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    # "Shanghai Jiao Tong" and "Shanghai Jiaotong" are both common.
    return " ".join(word for word in text.split() if word != "the").replace("jiao tong",
                                                                          "jiaotong")


def load_terms(path: str | None) -> dict[str, list[str]]:
    """{affiliation: [search terms]} from `affiliation,terms` (terms split by
    ';'); {} if absent. A row with blank terms is skipped."""
    if not path or not os.path.exists(path):
        return {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = {"affiliation", "terms"} - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"{path}: needs the column(s) {', '.join(sorted(missing))}")
        return {row["affiliation"].strip(): [t.strip() for t in row["terms"].split(";")]
                for row in reader if row["terms"].strip()}


def search_terms(affiliation: str, overrides: dict[str, list[str]]) -> list[str]:
    terms = overrides.get(affiliation, [affiliation])
    terms = [normalize(term) for term in terms]
    for term in list(terms):
        terms += ABBREVIATIONS.get(term, [])
    return list(dict.fromkeys(term for term in terms if term))


def term_pattern(term: str) -> re.Pattern:
    # "nanjing university" must not match "nanjing university of science and technology".
    prefix = "".join(rf"(?<!\b{word} )" for word in NOT_PRECEDED_BY.get(term, []))
    suffix = r"(?! of\b)" if term.endswith("university") else ""
    return re.compile(rf"{prefix}\b{re.escape(term)}\b{suffix}")


def email_domain(email: str) -> str:
    """Institutional part of an email domain: cs.vt.edu -> vt.edu, ict.ac.cn -> ict.ac.cn."""
    domain = email.strip().lower().rpartition("@")[2]
    labels = domain.split(".")
    keep = 3 if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in GENERIC_SLDS else 2
    domain = ".".join(labels[-keep:])
    return "" if domain in PUBLIC_EMAIL_DOMAINS else domain
