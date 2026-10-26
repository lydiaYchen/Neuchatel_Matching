"""The `reserve:` section of the run config, and the files of a reserve run.

The reserve reviewers share config.yaml with the matching and the desk-reject
check: `name` picks the run directory, `inputs.papers` is the paper JSON after
the desk rejects, `inputs.authors` and `inputs.pcinfo` are the authors CSV and
PC user export, and `deskreject.papers` and `deskreject.decisions` are the
paper JSON at the deadline and the desk-reject decisions. Everything else is
under `reserve:`, which states only what differs from DEFAULTS; an unknown key
is an error. Paths are relative to the repository root.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from deskreject import config as deskreject_config
from deskreject.config import RESERVE_FIELD
from matching.config import ROOT, _merge

DEFAULTS: dict = {
    "field": RESERVE_FIELD,      # the submission field naming the reserve reviewer
    "decisions": "curated/reserve_decisions.csv",  # the extracted list with a Decision column
    "cache": "cache/reserve",    # ORCID, ROR and OpenAlex responses, one file each
}


def load(path: str) -> dict:
    """The `reserve:` keys, plus `name`, `pcinfo`, `papers`, `authors`,
    `deadline_papers` and `desk_rejects` from a run config."""
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    cfg = _merge(DEFAULTS, raw.get("reserve") or {}, " under reserve")
    inputs = raw.get("inputs") or {}
    cfg.update(name=raw.get("name"), pcinfo=inputs.get("pcinfo"), papers=inputs.get("papers"),
               authors=inputs.get("authors"))
    if not all(cfg[k] for k in ("name", "pcinfo", "papers", "authors", "field")):
        raise ValueError(f"{path}: needs name, inputs.pcinfo, inputs.papers, inputs.authors "
                         f"and reserve.field")
    desk = deskreject_config.load(path)
    cfg.update(deadline_papers=desk["papers"], desk_rejects=desk["decisions"])
    return cfg


class ReserveDir:
    """The files of one reserve run, all in `runs/<name>/reserve/`."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.config = root / "config.yaml"
        self.candidates = root / "reserve_reviewers.csv"
        self.pc_to_remove = root / "pc_to_remove.csv"
        self.final = root / "final_reserve_reviewers.csv"

    @classmethod
    def of(cls, cfg: dict) -> "ReserveDir":
        return cls(ROOT / "runs" / cfg["name"] / "reserve")
