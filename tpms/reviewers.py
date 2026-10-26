"""The reviewer info for TPMS: `Email,FirstName,LastName,min,max`.

One row per account of the PC user export, in its order. The paper load
comes from the account's HotCRP tags:

  - a chair gets 0 to 0;
  - otherwise the first rule of `tpms.limits` whose tag the account carries
    decides: its `min` and `max`, or with `leave_out: true` no row at all;
  - an account no rule names gets `tpms.default_limits`.

The email is changed where `email_changes` says so.

The extra reviewers come from the reserve step's final list
(`firstName,lastName,email`) instead, and all get `tpms.extra_limits`.
"""

from __future__ import annotations

import csv
import os
import sys
from collections import Counter
from pathlib import Path

from matching.hotcrp import tag_names, write_csv_atomically

HEADER = ("Email", "FirstName", "LastName", "min", "max")


def limits(tags: set[str], *, chair: bool, rules: list[dict],
           default: dict) -> tuple[int, int] | None:
    """(min, max) for one account, or None to leave it out."""
    if chair:
        return 0, 0
    for rule in rules:
        if rule["tag"].lower() in tags:
            return None if rule.get("leave_out") else (rule["min"], rule["max"])
    return default["min"], default["max"]


def write(src: str, dst: Path, *, chairs: set[str], changes: dict[str, str],
          rules: list[dict], default: dict) -> set[str]:
    """Write `dst` from the user export `src`; returns the reviewer emails written."""
    if not os.path.exists(src):
        raise FileNotFoundError(f"{src}: user export not found")
    with open(src, newline="", encoding="utf-8-sig") as f:
        accounts = list(csv.DictReader(f))
    out, seen = [], Counter()
    for row in accounts:
        email = row["email"].strip()
        new = changes.get(email, email)
        tags = tag_names(row.get("tags") or "")
        seen.update(tags)
        load = limits(tags, chair=email in chairs or new in chairs, rules=rules, default=default)
        if load is not None:
            out.append((new, row["given_name"], row["family_name"], *load))
    write_csv_atomically(str(dst), HEADER, out)
    print(f"[reviewers] tags in the export: "
          f"{', '.join(f'{t} ({n})' for t, n in sorted(seen.items())) or '-'}", file=sys.stderr)
    unmatched = sorted({r["tag"].lower() for r in rules} - set(seen))
    if unmatched:
        print(f"WARNING: no account carries {', '.join(unmatched)}; check the spelling, "
              f"and that the export was downloaded as chair", file=sys.stderr)
    emails = {row["email"].strip() for row in accounts}
    for what, unknown in (("email_changes", set(changes) - emails), ("chairs", chairs - emails)):
        if unknown:
            print(f"WARNING: {what} names {len(unknown)} address(es) not in the export: "
                  f"{', '.join(sorted(unknown))}", file=sys.stderr)
    for load, n in sorted(Counter((r[3], r[4]) for r in out).items()):
        print(f"[reviewers] {n} reviewer(s) with min {load[0]}, max {load[1]}", file=sys.stderr)
    print(f"[reviewers] {len(accounts) - len(out)} account(s) left out", file=sys.stderr)
    return {row[0] for row in out}


def write_extra(src: str, dst: Path, *, changes: dict[str, str], limits: dict) -> set[str]:
    """Write `dst` from the reserve step's final list `src`; returns the emails written."""
    if not os.path.exists(src):
        raise FileNotFoundError(f"{src}: no final reserve reviewers yet; run "
                                f"python -m reserve first")
    with open(src, newline="", encoding="utf-8-sig") as f:
        out = [(changes.get(row["email"].strip(), row["email"].strip()), row["firstName"],
                row["lastName"], limits["min"], limits["max"]) for row in csv.DictReader(f)]
    write_csv_atomically(str(dst), HEADER, out)
    print(f"[reviewers] {dst.name}: {len(out)} extra reviewer(s) with min {limits['min']}, "
          f"max {limits['max']}", file=sys.stderr)
    return {row[0] for row in out}
