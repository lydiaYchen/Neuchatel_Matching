"""Stage 3: assign reviewers to papers.

  1. Candidates: every reviewer with a fingerprint. A reviewer is eligible
     for a paper unless a conflict layer excludes the pair (`coi`); the
     similarity of an eligible pair is the dot product of the fingerprints.
  2. Caps: a reviewer's load (`caps`); per paper at most
     `max_extra_per_paper` extra and `max_pc_per_paper` PC reviewers, and
     `same_country_cap` reviewers from its majority-author country.
  3. PC anchor: every paper first takes its best eligible PC member.
  4. Fill: the remaining slots by deferred acceptance over everyone.
  5. Repair: any slot still empty is filled by moving reviewers along chains.
  6. Weak pairs: pairs below `weak_pair_below` are swapped away where a
     move keeps every limit and loses no similarity.
  7. Hand swaps from `curated.swaps`, checked like everything else.

Writes the HotCRP uploads, `conflicts.csv` and the report on stdout; a
summary of the self-checks, all of which must be 0, ends stderr.
"""

from __future__ import annotations

import sys
from collections import Counter, defaultdict

import numpy as np

from .. import coi, dblp, embedding, fingerprints, hotcrp, roster
from ..config import RunDir, path
from . import countries, report
from .matcher import assignment_phase, class_counts_of, count_blocking_pairs
from .repair import apply_swaps, fix_weak_pairs, repair_short_papers


def extra_submission_caps(reviewers, papers, rule: dict[int, int],
                          author_orcids: dict[int, set[str]]) -> dict[str, int]:
    """{email: cap} for each extra reviewer who authored at least N of the
    submissions (by email, or ORCID via the authors CSV): the cap of the
    largest N in `rule` reached. `cap_overrides.csv` wins over it."""
    authorship = [
        ({(a.get("email") or "").strip().lower() for a in paper.get("authors") or []} - {""},
         author_orcids.get(paper["pid"], set()))
        for paper in papers
    ]
    caps: dict[str, int] = {}
    for r in reviewers:
        if r.pool != roster.EXTRA or r.override_cap is not None:
            continue
        emails = {r.email, r.hotcrp_email.lower()}
        orcid = hotcrp.parse_orcid(dblp.orcid_of(r) or "")
        n = sum(1 for es, orcids in authorship if emails & es or (orcid and orcid in orcids))
        reached = [k for k in rule if k <= n]
        if reached:
            caps[r.email] = rule[max(reached)]
    return caps


def run(cfg: dict, rd: RunDir) -> int:
    a, caps_cfg = cfg["assignment"], cfg["caps"]
    target = a["reviewers_per_paper"]
    papers = hotcrp.load_papers(path(cfg["inputs"]["papers"]), frozenset(cfg["exclude_papers"]))
    paper_cache = fingerprints.build_papers(papers, str(rd.paper_fingerprints))

    accounts = hotcrp.load_exports([str(rd.pcinfo), str(rd.extra_pcinfo)])
    placements = roster.load_placements(path(cfg["curated"]["dblp_placements"]))
    cap_overrides = roster.load_cap_overrides(path(cfg["curated"]["cap_overrides"]))
    reviewers = {}
    vectors: dict[str, dict] = {}
    for pool, export in ((roster.PC, rd.pcinfo), (roster.EXTRA, rd.extra_pcinfo)):
        pool_reviewers = roster.load_reviewers(str(export), pool, placements=placements,
                                               cap_overrides=cap_overrides)
        pool_vectors = embedding.load_json(str(rd.fingerprints[pool]))
        missing = [r.email for r in pool_reviewers if r.email not in pool_vectors]
        if missing:
            print(f"WARNING: {len(missing)} {pool} reviewer(s) have no fingerprint and cannot "
                  f"be assigned: {', '.join(missing)}", file=sys.stderr)
        reviewers.update({r.email: r for r in pool_reviewers})
        vectors.update(pool_vectors)
    unmatched = sorted(set(cap_overrides) - set(reviewers))
    if unmatched:
        print(f"WARNING: cap_overrides.csv names {len(unmatched)} address(es) in neither "
              f"export: {', '.join(unmatched)}", file=sys.stderr)

    rule = caps_cfg["extra_by_submissions"] or {}
    if rule:
        submission_caps = extra_submission_caps(
            reviewers.values(), papers, rule,
            hotcrp.load_paper_author_orcids(path(cfg["inputs"]["authors"])))
        for email, cap in submission_caps.items():
            reviewers[email].override_cap = cap
        print("Extra reviewers' caps by submissions: "
              + ", ".join(f"{n} at {cap}" for cap, n in sorted(Counter(submission_caps.values()).items())),
              file=sys.stderr)

    def cap_of(r) -> int:
        if r.override_cap is not None:
            return r.override_cap
        return caps_cfg["extra"] if r.pool == roster.EXTRA else caps_cfg["pc"]

    # Candidates in fingerprint-cache order, which breaks ties between equal scores.
    candidates = [e for e in vectors if e in reviewers]
    matrix = np.array([vectors[e]["vector"] for e in candidates], dtype=np.float32)
    try:
        conflicts = coi.derive(
            papers, list(reviewers.values()), accounts,
            dblp_cache=str(rd.frozen["dblp"]), coauthor_years=a["coauthor_years"],
            authors_csv=path(cfg["inputs"]["authors"]), author_cache=str(rd.frozen["authors"]),
            veto_score=a["topic_veto"])
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    # Each paper's eligible reviewers, best first, and the similarity of every pair.
    prefs: dict[int, list[str]] = {}
    affinity: dict[tuple[str, int], float] = {}
    reviewer_cap: dict[str, int] = {}
    for p in papers:
        pid = p["pid"]
        excluded = conflicts.excluded(p)
        idx = [i for i, e in enumerate(candidates)
               if e not in excluded and reviewers[e].hotcrp_email.lower() not in excluded]
        pairs = []
        if idx:
            sims = matrix[idx] @ np.array(paper_cache[str(pid)]["vector"], dtype=np.float32)
            pairs = [(candidates[i], float(s)) for i, s in zip(idx, sims)]
        for email, s in pairs:
            affinity[(email, pid)] = s
            reviewer_cap[email] = cap_of(reviewers[email])
        prefs[pid] = [e for e, _ in sorted(pairs, key=lambda es: -es[1])]

    country_caps, reviewer_country, thin = countries.country_caps(
        papers, candidates, reviewers,
        countries.hotcrp_countries(accounts, path(cfg["inputs"]["authors"])),
        a["same_country_cap"])
    extra_members = frozenset(e for e in reviewer_cap if reviewers[e].pool == roster.EXTRA)
    pc_members = frozenset(reviewer_cap) - extra_members
    capped = [(extra_members, a["max_extra_per_paper"]), (pc_members, a["max_pc_per_paper"]),
              *((c.members, c.papers) for c in country_caps)]

    pids = [p["pid"] for p in papers]
    slates: dict[int, list[str]] = {pid: [] for pid in pids}
    used: dict[str, int] = defaultdict(int)
    assigned_via: dict[tuple[int, str], str] = {}

    def phase(label, phase_target, members):
        held, phase_prefs, phase_cap = assignment_phase(
            pids, prefs, phase_target, slates, used, reviewer_cap, affinity, members, capped)
        for pid, emails in held.items():
            for e in emails:
                assigned_via[(pid, e)] = label
        return held, phase_prefs, phase_cap

    if extra_members:
        phase("PC anchor", {pid: min(1, target) for pid in pids}, pc_members)
    seed = class_counts_of(slates, pids, capped)
    fill_target = {pid: target - len(slates[pid]) for pid in pids}
    held, fill_prefs, fill_cap = phase("fill", fill_target, set(reviewer_cap))
    # Stability is guaranteed only where the class caps do not cross: a
    # country-capped paper also carries the PC/extra caps, so it is judged apart.
    capped_pids = {pid for c in country_caps for pid in c.papers}
    pairs1 = {pid: [(e, affinity[(e, pid)]) for e in fill_prefs[pid]] for pid in pids}
    blocking = count_blocking_pairs({pid: v for pid, v in pairs1.items() if pid not in capped_pids},
                                    held, fill_cap, fill_target, affinity, capped, seed)
    capped_blocking = count_blocking_pairs({pid: v for pid, v in pairs1.items() if pid in capped_pids},
                                           held, fill_cap, fill_target, affinity, capped, seed)

    chains = repair_short_papers(pids, prefs, {pid: target for pid in pids}, slates, used,
                                 reviewer_cap, capped)
    for chain in chains:
        for i, (pid, e) in enumerate(chain):
            assigned_via[(pid, e)] = "repair"
            if i + 1 < len(chain):
                assigned_via.pop((chain[i + 1][0], e), None)
    print(f"Repair: {len(chains)} empty slot(s) filled by moving "
          f"{sum(len(c) - 1 for c in chains)} assignment(s)", file=sys.stderr)
    moves = fix_weak_pairs(pids, slates, used, reviewer_cap, prefs, affinity, capped,
                           a["weak_pair_below"], assigned_via)
    print(f"Weak pairs (below {a['weak_pair_below']}): {len(moves)} move(s)", file=sys.stderr)
    for p, x, q, y, z in moves:
        print(f"  [{p}] {x} -> {y} ({affinity[(x, p)]:.3f} -> {affinity[(y, p)]:.3f}); "
              f"[{q}] {y} -> {z} ({affinity[(y, q)]:.3f} -> {affinity[(z, q)]:.3f})",
              file=sys.stderr)
    if cfg["curated"]["swaps"]:
        try:
            swaps = apply_swaps(path(cfg["curated"]["swaps"]), slates, used, reviewer_cap, prefs,
                                capped, assigned_via)
        except ValueError as exc:
            print(f"ERROR: hand swaps: {exc}", file=sys.stderr)
            return 1
        print(f"Hand swaps: {len(swaps)} replacement(s)", file=sys.stderr)
        for pid, out, new in swaps:
            print(f"  [{pid}] {out} -> {new} ({affinity[(out, pid)]:.3f} -> "
                  f"{affinity[(new, pid)]:.3f})", file=sys.stderr)

    report.paper_slates(papers, slates, affinity, reviewers, target)
    report.match_goodness(papers, slates, affinity)
    over_extra, over_pc = report.composition(papers, slates, extra_members,
                                             a["max_extra_per_paper"], a["max_pc_per_paper"])
    over_country = report.country_cap(papers, slates, country_caps, reviewer_country, thin,
                                      a["same_country_cap"])
    report.conflicts(papers, conflicts)
    n_changed = report.relaxed(papers, slates, assigned_via, affinity, reviewers)
    report.reviewer_papers(papers, slates, affinity, reviewers)

    load = Counter(e for emails in slates.values() for e in emails)
    over_cap = sum(1 for e, n in load.items() if n > cap_of(reviewers[e]))
    conflicted = sum(1 for p in papers for e in slates[p["pid"]]
                     if {e, reviewers[e].hotcrp_email.lower()} & conflicts.excluded(p))
    unfilled = sum(max(0, target - len(slates[pid])) for pid in pids)
    print(f"\nDone. {sum(load.values())} reviewer-paper pairs on {len(papers)} papers, "
          f"{len(load)} reviewers used. Must all be 0: {over_cap} reviewers over their cap; "
          f"{conflicted} conflicted pairs; {over_extra} papers over the extra cap; "
          f"{over_pc} over the PC cap; {over_country} over the same-country cap; "
          f"{blocking} blocking pairs; {unfilled} slots unfilled. "
          f"For information: {capped_blocking} blocking pairs on country-capped papers "
          f"(not guaranteed 0); {n_changed} papers changed after the matching.",
          file=sys.stderr)

    rows = coi.conflict_rows(papers, candidates, reviewers, conflicts, a["topic_veto"])
    unexplained, extra = coi.unexplained_exclusions(rows, candidates, prefs)
    if unexplained or extra:
        print(f"ERROR: conflicts.csv would not match the matcher ({unexplained} excluded pairs "
              f"unexplained, {extra} listed pairs not excluded)", file=sys.stderr)
        return 1
    hotcrp.write_csv_atomically(str(rd.conflicts), coi.CONFLICTS_HEADER, rows)
    if unfilled:
        print("ERROR: the assignment is incomplete; no upload written", file=sys.stderr)
        return 1
    hotcrp.write_upload(str(rd.upload), slates, affinity, reviewers)
    for pool, upload in rd.pool_uploads.items():
        members = {e for e, r in reviewers.items() if r.pool == pool}
        hotcrp.write_upload(str(upload), {pid: [e for e in emails if e in members]
                                          for pid, emails in slates.items()},
                            affinity, reviewers, clear_emails=sorted(members))
    return 0
