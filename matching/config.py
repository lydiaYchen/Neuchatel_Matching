"""A run: its YAML config and the files in its run directory.

A config states only what differs from DEFAULTS; an unknown key is an error.
Paths are relative to the repository root. See config.yaml for the EuroSys
2027 fall settings and README.md for what each one means.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

DEFAULTS: dict = {
    "name": None,
    "inputs": {
        "pcinfo": None,          # user export of the PC members
        "extra_pcinfo": None,    # user export of the extra reviewers
        "papers": None,          # paper JSON (downloaded as chair)
        "authors": None,         # authors CSV
    },
    "curated": {
        "dblp_placements": "curated/dblp_placements.csv",
        "fingerprint_overrides": "curated/fingerprint_overrides.csv",
        "cap_overrides": "curated/cap_overrides.csv",
        "swaps": None,
    },
    "caches": {
        "dblp": "cache/dblp_orcid_cache.json",
        "abstracts": "cache/openalex_abstracts.json",
        "authors": "cache/dblp_author_orcids.json",
    },
    "exclude_tags": [],
    "exclude_papers": [],
    "fingerprints": {
        "years": 4,
        "fallback_papers": 10,
        "fallback_years": 6,
    },
    "caps": {
        "pc": 4,
        "extra": 5,
        "extra_by_submissions": {},
    },
    "assignment": {
        "reviewers_per_paper": 4,
        "max_extra_per_paper": 3,
        "max_pc_per_paper": 3,
        "same_country_cap": 1,
        "coauthor_years": 5,
        "topic_veto": -2,
        "weak_pair_below": 0.92,
    },
    "tpms": None,                # read by tpms/config.py
    "reserve": None,             # read by reserve/config.py
    "deskreject": None,          # read by deskreject/config.py
    "conflicts": None,           # read by conflicts/config.py
}


def _merge(defaults: dict, overrides: dict, where: str) -> dict:
    unknown = set(overrides) - set(defaults)
    if unknown:
        raise ValueError(f"unknown key(s) {', '.join(sorted(unknown))}{where}")
    out = dict(defaults)
    for key, value in overrides.items():
        if isinstance(defaults[key], dict) and key != "extra_by_submissions":
            out[key] = _merge(defaults[key], value or {}, f" under {key}")
        else:
            out[key] = value
    return out


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        cfg = _merge(DEFAULTS, yaml.safe_load(f) or {}, "")
    missing = [f"inputs.{k}" for k, v in cfg["inputs"].items() if not v]
    if not cfg["name"] or missing:
        raise ValueError(f"{path}: needs a name and {', '.join(missing) or 'all inputs'}")
    if cfg["assignment"]["topic_veto"] not in (-1, -2):
        raise ValueError(f"{path}: assignment.topic_veto must be -1 or -2")
    rule = cfg["caps"]["extra_by_submissions"] or {}
    if not all(type(n) is int and type(c) is int and n >= 1 and c >= 0 for n, c in rule.items()):
        raise ValueError(f"{path}: caps.extra_by_submissions maps a submission count to a cap, "
                         f"e.g. {{2: 7, 3: 8}}")
    return cfg


def path(value: str | None) -> str | None:
    """A config path, resolved against the repository root."""
    if value is None:
        return None
    p = Path(value)
    return str(p if p.is_absolute() else ROOT / p)


class RunDir:
    """The files of one run, all in `runs/<name>/`."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.config = root / "config.yaml"
        self.pcinfo = root / "pcinfo.csv"                  # the exports as assigned from
        self.extra_pcinfo = root / "extra_pcinfo.csv"
        self.fingerprints = {"pc": root / "fingerprints.json",
                             "extra": root / "extra_fingerprints.json"}
        self.basis = {"pc": root / "fingerprint_basis.json",
                      "extra": root / "extra_fingerprint_basis.json"}
        self.paper_fingerprints = root / "paper_fingerprints.json"
        # The shared caches as this run used them (see pipeline.freeze_caches).
        self.frozen = {"dblp": root / "dblp_orcid_cache.json",
                       "abstracts": root / "openalex_abstracts.json",
                       "authors": root / "dblp_author_orcids.json"}
        self.upload = root / "assignment.csv"
        self.pool_uploads = {"pc": root / "pc_assignment.csv",
                             "extra": root / "extra_assignment.csv"}
        self.report = root / "assignment.txt"
        self.log = root / "assign.log"
        self.conflicts = root / "conflicts.csv"
        self.reviewers = root / "reviewers.csv"
        self.publications = root / "publications.csv"

    @classmethod
    def of(cls, cfg: dict) -> "RunDir":
        return cls(ROOT / "runs" / cfg["name"])
