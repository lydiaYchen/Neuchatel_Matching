"""The `tpms:` section of the run config, and the files of a TPMS run.

TPMS shares config.yaml with the matching: `name` picks the run directory and
`inputs.pcinfo` is the PC members' user export. The extra reviewers are the
reserve step's `final_reserve_reviewers.csv` of the same run. Everything else
it reads is under `tpms:`, which states only what differs from DEFAULTS; an
unknown key is an error. Paths are relative to the repository root.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from matching.config import ROOT, _merge

DEFAULTS: dict = {
    "papers": None,              # submissions zip (eurosys27-fall-paper<id>.pdf)
    "pcconflicts": None,         # PC conflicts CSV
    "chairs": "curated/chairs.csv",              # email: left out of the conflicts, no papers
    "email_changes": "curated/email_changes.csv",  # old_email,new_email
    "limits": [],                # [{tag, min, max} or {tag, leave_out: true}], first match wins
    "default_limits": {"min": None, "max": None},
    "extra_pcconflicts": None,   # the extra reviewers' conflicts CSV; none: no extra files
    "extra_limits": {"min": None, "max": None},  # papers per extra reviewer
}


def _check_limits(min_: object, max_: object, where: str) -> None:
    if not (type(min_) is int and type(max_) is int and 0 <= min_ <= max_):
        raise ValueError(f"{where}: min and max must be whole numbers with 0 <= min <= max")


def load(path: str) -> dict:
    """{name, pcinfo, and the `tpms:` keys} from a run config."""
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    cfg = _merge(DEFAULTS, raw.get("tpms") or {}, " under tpms")
    cfg["name"] = raw.get("name")
    cfg["pcinfo"] = (raw.get("inputs") or {}).get("pcinfo")
    missing = [k for k in ("name", "pcinfo", "papers", "pcconflicts") if not cfg[k]]
    if missing:
        raise ValueError(f"{path}: needs {', '.join(missing)} (name, inputs.pcinfo, "
                         f"tpms.papers, tpms.pcconflicts)")
    for i, rule in enumerate(cfg["limits"]):
        where = f"{path}: tpms.limits[{i}]"
        if not isinstance(rule, dict) or not rule.get("tag"):
            raise ValueError(f"{where} needs a tag")
        if rule.get("leave_out") is True and set(rule) == {"tag", "leave_out"}:
            continue
        if set(rule) != {"tag", "min", "max"}:
            raise ValueError(f"{where} is either {{tag, min, max}} or {{tag, leave_out: true}}")
        _check_limits(rule["min"], rule["max"], where)
    d = cfg["default_limits"]
    _check_limits(d["min"], d["max"], f"{path}: tpms.default_limits")
    if cfg["extra_pcconflicts"]:
        e = cfg["extra_limits"]
        _check_limits(e["min"], e["max"], f"{path}: tpms.extra_limits")
    return cfg


class TpmsDir:
    """The files of one TPMS run, all in `runs/<name>/tpms/`."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.config = root / "config.yaml"
        self.papers = root / "papers.zip"
        self.pcconflicts = root / "pcconflicts.csv"
        self.reviewer_info = root / "reviewer_info.csv"
        self.extra_pcconflicts = root / "extra_pcconflicts.csv"
        self.extra_reviewer_info = root / "extra_reviewer_info.csv"

    @classmethod
    def of(cls, cfg: dict) -> "TpmsDir":
        return cls(ROOT / "runs" / cfg["name"] / "tpms")
