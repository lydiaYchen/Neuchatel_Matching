"""Make the TPMS files from one config.

    uv run python -m tpms config.yaml

Every file lands in `runs/<name>/tpms/`:

  1. papers       the submissions zip with each PDF renamed to
                  `paper<id>.pdf` (`papers.zip`).
  2. conflicts    the PC conflicts without the chairs (`pcconflicts.csv`).
  3. reviewers    each PC member's paper load from their HotCRP tags
                  (`reviewer_info.csv`).

With `tpms.extra_pcconflicts` set, the same two CSVs for the extra reviewers,
who are the reserve step's final list (`extra_pcconflicts.csv`,
`extra_reviewer_info.csv`).

The chairs and email changes come from two hand-made files; see README.md.
"""

from __future__ import annotations

import argparse
import shutil
import sys

from matching.config import path
from reserve.config import ReserveDir

from . import config, conflicts, emails, papers, reviewers
from .config import TpmsDir


def check_emails(conflicted: set[str], reviewers: set[str], what: str) -> None:
    """Warn about conflicts of addresses TPMS will have no reviewer for."""
    known = {e.lower() for e in reviewers}
    unknown = sorted(e for e in conflicted if e.lower() not in known)
    if unknown:
        print(f"WARNING: {what} names {len(unknown)} address(es) not in the reviewer info, "
              f"so TPMS cannot apply their conflicts. For a reviewer whose HotCRP account has "
              f"another address, add old_email,new_email to email_changes: "
              f"{', '.join(unknown)}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("config", help="YAML config, e.g. config.yaml")
    args = parser.parse_args()
    try:
        cfg = config.load(args.config)
        chairs = emails.load_chairs(path(cfg["chairs"]))
        changes = emails.load_email_changes(path(cfg["email_changes"]))
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    td = TpmsDir.of(cfg)
    td.root.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.config, td.config)
    print(f"== {cfg['name']} (TPMS): {td.root}", file=sys.stderr)
    print(f"[emails] {len(chairs)} chair(s), {len(changes)} email change(s)", file=sys.stderr)

    try:
        papers.rename(path(cfg["papers"]), td.papers)
        conflicted = conflicts.write(path(cfg["pcconflicts"]), td.pcconflicts, chairs=chairs,
                                     changes=changes)
        written = reviewers.write(path(cfg["pcinfo"]), td.reviewer_info, chairs=chairs,
                                  changes=changes, rules=cfg["limits"],
                                  default=cfg["default_limits"])
        check_emails(conflicted, written, td.pcconflicts.name)
        uploads = [td.papers, td.pcconflicts, td.reviewer_info]
        if cfg["extra_pcconflicts"]:
            conflicted = conflicts.write(path(cfg["extra_pcconflicts"]), td.extra_pcconflicts,
                                         chairs=chairs, changes=changes)
            written = reviewers.write_extra(str(ReserveDir.of(cfg).final), td.extra_reviewer_info,
                                            changes=changes, limits=cfg["extra_limits"])
            check_emails(conflicted, written, td.extra_pcconflicts.name)
            uploads += [td.extra_pcconflicts, td.extra_reviewer_info]
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"\n== {cfg['name']} done. Upload {', '.join(u.name for u in uploads)} from "
          f"{td.root}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
