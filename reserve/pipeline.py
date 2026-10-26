"""Find the reserve reviewers the papers named, and finalise the ones chosen.

Run it after the desk rejects have been decided (`deskreject.decisions`).

    uv run python -m reserve config.yaml

Every file lands in `runs/<name>/reserve/`:

  1. extract    every reserve reviewer the papers named at the deadline, one
                row per person, less anyone a desk-rejected paper named, with
                hints for deciding (network: ORCID, ROR, OpenAlex; cached)
                (`reserve_reviewers.csv`). The PC members a desk-rejected
                paper named, to remove in HotCRP (`pc_to_remove.csv`).
  2. finalise   only once the decided list exists: the reserve reviewers
                chosen, not on the PC, with a paper still submitted
                (`final_reserve_reviewers.csv`).

Between the two a person decides: copy `reserve_reviewers.csv` to
`reserve.decisions`, fill in its Decision column with Y or N, and rerun.
See README.md.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from deskreject import config as deskreject_config
from matching.config import path

from . import config, extract, finalise
from .config import ReserveDir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("config", help="YAML config, e.g. config.yaml")
    args = parser.parse_args()
    try:
        cfg = config.load(args.config)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    rd = ReserveDir.of(cfg)
    rd.root.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.config, rd.config)
    print(f"== {cfg['name']} (reserve reviewers): {rd.root}", file=sys.stderr)

    decisions, desk_rejects = path(cfg["decisions"]), path(cfg["desk_rejects"])
    try:
        if not os.path.exists(desk_rejects):
            raise FileNotFoundError(f"{desk_rejects}: no desk-reject decisions yet; run "
                                    f"python -m deskreject first and decide")
        desk_rejected = deskreject_config.load_papers(desk_rejects, decided=True)
        print(f"[extract] {len(desk_rejected)} desk-rejected paper(s)", file=sys.stderr)
        extract.run(path(cfg["deadline_papers"]), cfg["field"], desk_rejected,
                    path(cfg["authors"]), path(cfg["pcinfo"]), Path(path(cfg["cache"])),
                    rd.candidates, rd.pc_to_remove)
        if not os.path.exists(decisions):
            print(f"\n== Next: copy {rd.candidates} to {cfg['decisions']}, fill in its "
                  f"Decision column with Y or N, and rerun", file=sys.stderr)
            return 0
        finalise.run(decisions, path(cfg["papers"]), path(cfg["authors"]), rd.final)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"\n== {cfg['name']} done. The final reserve reviewers are in {rd.final}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
