"""The `conflicts:` section of the run config, and the files of a conflicts run.

The conflict uploads share config.yaml with the matching: `name` picks the
run directory, `inputs.pcinfo` and `inputs.extra_pcinfo` are the user exports
of the two pools, and `inputs.papers` is the paper JSON. Everything else is
under `conflicts:`, which states only what differs from DEFAULTS; an unknown
key is an error. A step whose input is not set is skipped. Paths are relative
to the repository root.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from matching.config import ROOT, _merge

DEFAULTS: dict = {
    "pc_page": None,             # the PC site's Assignments -> Conflicts page, saved as HTML
    "extra_page": None,          # the same page of the extra reviewers' site
    "extra_pcconflicts": None,   # the extra reviewers' conflicts CSV, as HotCRP has them now
    "affiliation_terms": "curated/affiliation_terms.csv",  # affiliation,terms
}


def load(path: str) -> dict:
    """The `conflicts:` keys, plus `name`, `pcinfo`, `extra_pcinfo` and `papers`."""
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    cfg = _merge(DEFAULTS, raw.get("conflicts") or {}, " under conflicts")
    inputs = raw.get("inputs") or {}
    cfg.update(name=raw.get("name"), pcinfo=inputs.get("pcinfo"),
               extra_pcinfo=inputs.get("extra_pcinfo"), papers=inputs.get("papers"))
    needed = {"name": "name"}
    if cfg["pc_page"]:
        needed["pcinfo"] = "inputs.pcinfo"
    if cfg["extra_page"] or cfg["extra_pcconflicts"]:
        needed["extra_pcinfo"] = "inputs.extra_pcinfo"
    if cfg["extra_pcconflicts"]:
        needed["papers"] = "inputs.papers"
    missing = [label for key, label in needed.items() if not cfg[key]]
    if missing:
        raise ValueError(f"{path}: needs {', '.join(missing)}")
    return cfg


class ConflictsDir:
    """The files of one conflicts run, all in `runs/<name>/conflicts/`."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.config = root / "config.yaml"
        self.pages = {"pc": root / "pc_page_conflicts.csv",
                      "extra": root / "extra_page_conflicts.csv"}
        self.affiliation = root / "extra_affiliation_conflicts.csv"
        self.evidence = root / "extra_affiliation_evidence.csv"

    @classmethod
    def of(cls, cfg: dict) -> "ConflictsDir":
        return cls(ROOT / "runs" / cfg["name"] / "conflicts")
