"""Flag the papers to look at for a desk reject.

    uv run python -m deskreject config.yaml

Run it on the paper JSON at the deadline (`deskreject.papers`). It writes
`runs/<name>/deskreject/flagged.csv`: each flagged paper with its reasons.
A person then decides: copy it to `deskreject.decisions`, fill in its
Decision column with Y (desk reject) or N, and desk-reject the Y papers in
HotCRP. The reserve reviewers read the same decisions. See README.md.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys

from matching.config import path

from . import check, config
from .config import DeskRejectDir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("config", help="YAML config, e.g. config.yaml")
    args = parser.parse_args()
    try:
        cfg = config.load(args.config)
        exempted = config.load_papers(path(cfg["exemptions"]))
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    dd = DeskRejectDir.of(cfg)
    dd.root.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.config, dd.config)
    print(f"== {cfg['name']} (desk rejects): {dd.root}", file=sys.stderr)
    print(f"[deskreject] {len(exempted)} approved exemption(s)", file=sys.stderr)

    try:
        check.run(cfg, path(cfg["papers"]), path(cfg["authors"]), path(cfg["pcinfo"]),
                  path(cfg["formatcheck"]), exempted, dd.flagged)
        decisions = path(cfg["decisions"])
        if os.path.exists(decisions):
            rejected = config.load_papers(decisions, decided=True)
            print(f"\n== {cfg['name']} done. {cfg['decisions']} desk-rejects {len(rejected)} "
                  f"paper(s)", file=sys.stderr)
        else:
            print(f"\n== Next: copy {dd.flagged} to {cfg['decisions']}, fill in its Decision "
                  f"column with Y or N, and desk-reject the Y papers in HotCRP", file=sys.stderr)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
