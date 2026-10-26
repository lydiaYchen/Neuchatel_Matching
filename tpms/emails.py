"""The two hand-made email lists. Both are optional: a missing file is empty.

  chairs         `email`: the chairs. Left out of the conflicts and given no
                 papers.
  email_changes  `old_email,new_email`: a reviewer whose TPMS account is under
                 another address than their HotCRP one. A row with a blank
                 value is skipped.
"""

from __future__ import annotations

import csv
import os


def _rows(path: str | None, fields: tuple[str, ...]) -> list[dict[str, str]]:
    if not path or not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = set(fields) - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"{path}: needs the column(s) {', '.join(sorted(missing))}")
        return [{k: (row[k] or "").strip() for k in fields} for row in reader]


def load_chairs(path: str | None) -> set[str]:
    return {row["email"] for row in _rows(path, ("email",)) if row["email"]}


def load_email_changes(path: str | None) -> dict[str, str]:
    return {row["old_email"]: row["new_email"]
            for row in _rows(path, ("old_email", "new_email"))
            if row["old_email"] and row["new_email"]}
