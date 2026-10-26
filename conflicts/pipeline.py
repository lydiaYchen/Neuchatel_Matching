"""Make the conflict uploads for HotCRP from one config.

    uv run python -m conflicts config.yaml

Every file lands in `runs/<name>/conflicts/`; each step runs only when its
input is set in `conflicts:`:

  1. pages         the potential conflicts on each site's Assignments ->
                   Conflicts page (`pc_page_conflicts.csv`,
                   `extra_page_conflicts.csv`).
  2. affiliations  the extra reviewers who share an affiliation or email
                   domain with a paper's authors, less what HotCRP already
                   has (`extra_affiliation_conflicts.csv`, and the evidence
                   `extra_affiliation_evidence.csv`).

Each upload is a HotCRP bulk assignment (Assignments -> Upload). See README.md.
"""

from __future__ import annotations

import argparse
import shutil
import sys

from matching.config import path

from . import affiliations, config, pages
from .config import ConflictsDir
from .institutions import load_terms


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("config", help="YAML config, e.g. config.yaml")
    args = parser.parse_args()
    try:
        cfg = config.load(args.config)
        terms = load_terms(path(cfg["affiliation_terms"]))
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    cd = ConflictsDir.of(cfg)
    cd.root.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.config, cd.config)
    print(f"== {cfg['name']} (conflicts): {cd.root}", file=sys.stderr)

    uploads = []
    try:
        for pool, page, export in (("pc", cfg["pc_page"], cfg["pcinfo"]),
                                   ("extra", cfg["extra_page"], cfg["extra_pcinfo"])):
            if page:
                pages.run(path(page), path(export), cd.pages[pool])
                uploads.append(cd.pages[pool])
        if cfg["extra_pcconflicts"]:
            affiliations.run(path(cfg["extra_pcinfo"]), path(cfg["extra_pcconflicts"]),
                             path(cfg["papers"]), terms, cd.affiliation, cd.evidence)
            uploads.append(cd.affiliation)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if not uploads:
        print("Nothing to do: set conflicts.pc_page, extra_page or extra_pcconflicts",
              file=sys.stderr)
        return 0
    print(f"\n== {cfg['name']} done. Upload {', '.join(u.name for u in uploads)} from "
          f"{cd.root} (Assignments -> Upload)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
