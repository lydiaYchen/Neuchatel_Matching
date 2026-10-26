"""The `deskreject:` section of the run config, and the files of a desk-reject run.

The desk-reject check shares config.yaml with the matching: `name` picks the
run directory, `inputs.authors` and `inputs.pcinfo` are the authors CSV and
PC user export, and `reserve.field` names the reserve reviewer field.
Everything else is under `deskreject:`, which states only what differs from
DEFAULTS; an unknown key is an error. Paths are relative to the repository
root.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

import yaml

from matching.config import ROOT, _merge

# The submission field naming the reserve reviewer; `reserve.field` overrides it.
RESERVE_FIELD = "Reserve reviewer"

DEFAULTS: dict = {
    "papers": None,              # paper JSON at the deadline, before any desk reject
    "formatcheck": None,         # HotCRP's format check CSV
    "exemptions": "curated/reserve_exemptions.csv",  # paper: exemptions the chairs approved
    "decisions": "curated/desk_rejects.csv",         # the flagged list with Decision filled in
    "max_submissions": 3,        # an author on more submissions than this is flagged
    "margin_over": 5,            # margins too small by more than this percentage are serious
}


def load(path: str) -> dict:
    """The `deskreject:` keys, plus `name`, `pcinfo`, `authors` and `field` from a run config."""
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    cfg = _merge(DEFAULTS, raw.get("deskreject") or {}, " under deskreject")
    inputs = raw.get("inputs") or {}
    cfg.update(name=raw.get("name"), pcinfo=inputs.get("pcinfo"), authors=inputs.get("authors"),
               field=(raw.get("reserve") or {}).get("field") or RESERVE_FIELD)
    if not all(cfg[k] for k in ("name", "pcinfo", "authors", "papers", "formatcheck")):
        raise ValueError(f"{path}: needs name, inputs.pcinfo, inputs.authors, deskreject.papers "
                         f"and deskreject.formatcheck")
    for key in ("max_submissions", "margin_over"):
        if type(cfg[key]) is not int or cfg[key] < 0:
            raise ValueError(f"{path}: deskreject.{key} must be a whole number")
    return cfg


def load_papers(path: str, column: str = "paper", decided: bool = False) -> set[int]:
    """Paper IDs from a hand-made CSV: every row, or with `decided` only the rows
    whose Decision is Y. A missing file is empty; a blank paper is skipped."""
    if not os.path.exists(path):
        return set()
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        needed = {column, "Decision"} if decided else {column}
        missing = needed - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"{path}: needs the column(s) {', '.join(sorted(missing))}")
        return {int(row[column]) for row in reader if row[column].strip()
                and (not decided or (row["Decision"] or "").strip().upper() == "Y")}


class DeskRejectDir:
    """The files of one desk-reject run, all in `runs/<name>/deskreject/`."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.config = root / "config.yaml"
        self.flagged = root / "flagged.csv"

    @classmethod
    def of(cls, cfg: dict) -> "DeskRejectDir":
        return cls(ROOT / "runs" / cfg["name"] / "deskreject")
