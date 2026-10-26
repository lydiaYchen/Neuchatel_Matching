"""The PC conflicts for TPMS: `paperID,reviewerEmail`.

From HotCRP's PC conflicts CSV (`paper,title,given_name,family_name,email,
conflicttype`): every row, with the email changed where `email_changes` says
so, and without the chairs.
"""

from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

from matching.hotcrp import write_csv_atomically

HEADER = ("paperID", "reviewerEmail")


def write(src: str, dst: Path, *, chairs: set[str], changes: dict[str, str]) -> set[str]:
    """Write `dst` from `src`; returns the reviewer emails it names."""
    if not os.path.exists(src):
        raise FileNotFoundError(f"{src}: PC conflicts CSV not found")
    with open(src, newline="", encoding="utf-8-sig") as f:
        rows = [(row["paper"].strip(), row["email"].strip()) for row in csv.DictReader(f)]
    kept = []
    for paper, email in rows:
        email = changes.get(email, email)
        if email not in chairs:
            kept.append((paper, email))
    write_csv_atomically(str(dst), HEADER, kept)
    print(f"[conflicts] {dst.name}: {len(kept)} conflict(s), {len(rows) - len(kept)} of the "
          f"chairs left out", file=sys.stderr)
    return {email for _, email in kept}
