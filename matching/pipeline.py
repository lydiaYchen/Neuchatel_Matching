"""Run the whole assignment from one config.

    uv run python -m matching config.yaml                       # full run
    uv run python -m matching config.yaml --skip-fingerprints   # reuse the run's fingerprints

Every file of a run lands in `runs/<name>/`:

  1. roster        the two user exports, minus accounts carrying a tag in
                   `exclude_tags` (`pcinfo.csv`, `extra_pcinfo.csv`).
  2. fingerprints  each reviewer's DBLP papers (network: DBLP, OpenAlex) and
                   their SPECTER2 fingerprint; the paper authors placed on
                   DBLP. The shared caches are then frozen into the run
                   directory, so the run holds every piece of data it used.
  3. assignment    the matching, the uploads (`assignment.csv`,
                   `pc_assignment.csv`, `extra_assignment.csv`), `conflicts.csv`,
                   the report `assignment.txt` and the log `assign.log`.
  4. records       `reviewers.csv` and `publications.csv`.

`--skip-fingerprints` reruns from what the run directory already holds:
nothing is fetched or embedded, the frozen caches are read as they are, and
the result is the same as the run that built them.
"""

from __future__ import annotations

import argparse
import csv
import os
import shutil
import sys
from contextlib import redirect_stdout

from . import assign, authors, config, fingerprints, hotcrp, records, roster
from .config import ROOT, RunDir, path


def load_env() -> None:
    """KEY=VALUE lines of the gitignored `.env` (OPENALEX_API_KEY), below the environment."""
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip().removeprefix("export ").strip(),
                                  value.strip().strip("'\""))


def copy_export(src: str, dst, exclude: list[str]) -> set[str]:
    """Copy a user export without the accounts tagged with one of `exclude`;
    returns the tags it saw."""
    if not os.path.exists(src):
        raise FileNotFoundError(f"{src}: user export not found")
    wanted = hotcrp.tag_names(" ".join(exclude))
    with open(src, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fields, rows = reader.fieldnames, list(reader)
    seen: set[str] = set()
    kept = []
    for row in rows:
        tags = hotcrp.tag_names(row.get("tags") or "")
        seen |= tags
        if not tags & wanted:
            kept.append(row)
    with open(dst, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(kept)
    print(f"[roster] {os.path.basename(src)}: {len(rows) - len(kept)} account(s) left out "
          f"for tags {', '.join(exclude) or '-'}", file=sys.stderr)
    return seen


def freeze_caches(cfg: dict, rd: RunDir, *, refresh: bool) -> None:
    """Copy the shared caches into the run directory. `refresh` (after a
    fingerprint build) replaces earlier copies; otherwise they are kept."""
    for key, frozen in rd.frozen.items():
        shared = path(cfg["caches"][key])
        if os.path.exists(shared) and (refresh or not frozen.exists()):
            shutil.copyfile(shared, frozen)


class Tee:
    """Write to the terminal and a log file at once."""

    def __init__(self, stream, log) -> None:
        self.stream, self.log = stream, log

    def write(self, text: str) -> int:
        self.log.write(text)
        return self.stream.write(text)

    def flush(self) -> None:
        self.stream.flush()
        self.log.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("config", help="YAML config, e.g. config.yaml")
    parser.add_argument("--skip-fingerprints", action="store_true",
                        help="reuse the run directory's fingerprints and frozen caches")
    args = parser.parse_args()
    try:
        cfg = config.load(args.config)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    load_env()
    rd = RunDir.of(cfg)
    rd.root.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.config, rd.config)
    print(f"== {cfg['name']}: {rd.root}", file=sys.stderr)

    seen: set[str] = set()
    for src, dst in ((cfg["inputs"]["pcinfo"], rd.pcinfo), (cfg["inputs"]["extra_pcinfo"], rd.extra_pcinfo)):
        seen |= copy_export(path(src), dst, cfg["exclude_tags"])
    unmatched = hotcrp.tag_names(" ".join(cfg["exclude_tags"])) - seen
    if unmatched:
        print(f"WARNING: no account carries {', '.join(sorted(unmatched))}; check the spelling, "
              f"and that the exports were downloaded as chair", file=sys.stderr)

    a, fp = cfg["assignment"], cfg["fingerprints"]
    placements = roster.load_placements(path(cfg["curated"]["dblp_placements"]))
    overrides = fingerprints.load_overrides(path(cfg["curated"]["fingerprint_overrides"]))
    if not args.skip_fingerprints:
        if not os.environ.get("OPENALEX_API_KEY"):
            print("WARNING: OPENALEX_API_KEY is not set; OpenAlex allows ~1,000 requests a "
                  "day without it", file=sys.stderr)
    else:
        freeze_caches(cfg, rd, refresh=False)
    for pool, export in (("pc", rd.pcinfo), ("extra", rd.extra_pcinfo)):
        reviewers = roster.load_reviewers(str(export), pool, placements=placements,
                                          cap_overrides={})
        frozen = args.skip_fingerprints
        fingerprints.build_reviewers(
            reviewers, papers_path=path(cfg["inputs"]["papers"]),
            cache_path=str(rd.fingerprints[pool]), basis_path=str(rd.basis[pool]),
            dblp_cache=str(rd.frozen["dblp"]) if frozen else path(cfg["caches"]["dblp"]),
            abstract_cache=str(rd.frozen["abstracts"]) if frozen else path(cfg["caches"]["abstracts"]),
            placements=placements, overrides=overrides, years=fp["years"],
            fetch_years=max(a["coauthor_years"], fp["fallback_years"] if fp["fallback_papers"] else 0),
            fallback_papers=fp["fallback_papers"], fallback_years=fp["fallback_years"],
            basis_only=frozen,
        )
    if not args.skip_fingerprints:
        authors.place(path(cfg["inputs"]["authors"]), path(cfg["inputs"]["papers"]),
                      path(cfg["caches"]["authors"]))
        freeze_caches(cfg, rd, refresh=True)

    with open(rd.report, "w", encoding="utf-8") as out, open(rd.log, "w", encoding="utf-8") as log:
        stderr = sys.stderr
        sys.stderr = Tee(stderr, log)
        try:
            with redirect_stdout(out):
                code = assign.run(cfg, rd)
        finally:
            sys.stderr = stderr
    if code:
        print(f"ERROR: the assignment failed; see {rd.log}", file=sys.stderr)
        return code
    records.write(rd)
    print(f"\n== {cfg['name']} done. Upload {rd.pool_uploads['pc'].name} and "
          f"{rd.pool_uploads['extra'].name} (or {rd.upload.name}) from {rd.root}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
