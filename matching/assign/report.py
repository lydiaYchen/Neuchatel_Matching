"""The assignment report (`assignment.txt`) and the self-checks that must be 0.

Sections: every paper's reviewers best match first; match goodness (the mean
similarity of a paper's reviewers) worst first; the PC/extra mix; the
same-country cap; what each conflict layer excluded; the papers the repair,
weak-pair fix or a hand swap touched; every reviewer's papers.
"""

from __future__ import annotations

from collections import Counter, defaultdict


def paper_slates(papers, slates, affinity, reviewers, target: int) -> None:
    goodness = goodness_of(slates, affinity)
    for p in papers:
        pid = p["pid"]
        assigned = sorted(slates[pid], key=lambda e: -affinity[(e, pid)])
        short = "  *** UNDER-FILLED ***" if len(assigned) < target else ""
        g = goodness[pid]
        print(f"\n=== [{pid}] {p['title']}")
        print(f"    topics: {', '.join(p.get('topics', []))}")
        print(f"    assigned {len(assigned)} of {target}{short}")
        print(f"    match goodness: {'n/a' if g is None else format(g, '.3f')}")
        for rank, email in enumerate(assigned, 1):
            r = reviewers[email]
            print(f"  {rank:2d}. {affinity[(email, pid)]:.3f}  {r.name} <{email}>  "
                  f"[{r.pool}]  ({r.primary})")


def goodness_of(slates, affinity) -> dict[int, float | None]:
    return {pid: sum(affinity[(e, pid)] for e in emails) / len(emails) if emails else None
            for pid, emails in slates.items()}


def match_goodness(papers, slates, affinity) -> None:
    goodness = goodness_of(slates, affinity)
    scored = sorted((p for p in papers if goodness[p["pid"]] is not None),
                    key=lambda p: (goodness[p["pid"]], p["pid"]))
    print("\n=== Match goodness (mean similarity of assigned reviewers, worst first) ===")
    if scored:
        values = [goodness[p["pid"]] for p in scored]
        mean = sum(values) / len(values)
        std = (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5
        print(f"{len(scored)} paper(s): mean {mean:.4f}, std {std:.4f}, "
              f"worst 10 {sum(values[:10]) / min(10, len(values)):.4f}")
        for p in scored:
            print(f"  {goodness[p['pid']]:.3f}  [{p['pid']}] {p['title']}")


def composition(papers, slates, extra_members, max_extra: int, max_pc: int) -> tuple[int, int]:
    """Print the PC/extra mix; return (papers over the extra cap, over the PC cap)."""
    pc_counts = Counter()
    over_extra = over_pc = 0
    for p in papers:
        slate = slates[p["pid"]]
        n_extra = sum(1 for e in slate if e in extra_members)
        pc_counts[len(slate) - n_extra] += 1
        over_extra += n_extra > max_extra
        over_pc += len(slate) - n_extra > max_pc
    print("\n=== PC and extra reviewers per paper ===")
    print(f"At most {max_extra} extra and {max_pc} PC reviewer(s) per paper.")
    print("PC reviewers per paper: "
          + ", ".join(f"{n} PC: {c} paper(s)" for n, c in sorted(pc_counts.items())))
    return over_extra, over_pc


def country_cap(papers, slates, caps, reviewer_country, thin, cap: int) -> int:
    """Print how the same-country cap bound; return the papers over it."""
    placed = sum(1 for c in reviewer_country.values() if c)
    print("\n=== Same-country cap ===")
    print(f"At most {cap} reviewer(s) from a paper's majority-author country.")
    print(f"Reviewers with a known country: {placed} of {len(reviewer_country)}; papers "
          f"judged: {len(papers) - len(thin)} of {len(papers)} ({len(thin)} with too few "
          f"authors placed are not capped).")
    over = 0
    for c in sorted(caps, key=lambda c: (-len(c.papers), c.code)):
        counts = [sum(1 for e in slates[pid] if e in c.members) for pid in c.papers]
        at_cap = sum(n == cap for n in counts)
        over += sum(n > cap for n in counts)
        print(f"  {c.code}: {len(c.papers)} paper(s), {len(c.members)} reviewer(s), "
              f"{at_cap} paper(s) at the cap")
    return over


def conflicts(papers, conflicts_) -> None:
    """What each derived layer excluded, and whom it cannot see."""
    def pairs(layer) -> int:
        return sum(len(found) for found in layer.values())

    index = conflicts_.coauthor_index
    people = {(p.get("email") or "").strip().lower()
              for paper in papers
              for p in (paper.get("authors") or []) + (paper.get("contacts") or [])} - {""}
    by_match = Counter(c.match for found in conflicts_.coauthor.values() for c in found.values())
    print("\n=== Conflicts beyond those declared in HotCRP ===")
    print(f"Co-authors: {pairs(conflicts_.coauthor)} pair(s) ("
          + ", ".join(f"{n} {m}" for m, n in sorted(by_match.items())) + "); "
          f"{len(index.uncovered)} reviewer(s) without DBLP co-authors pass unchecked; "
          f"{len(people & set(conflicts_.author_pids))} of {len(people)} paper authors "
          f"identified on DBLP, the rest matched by name")
    print(f"Declared collaborators: {pairs(conflicts_.collaborator)} pair(s)")
    print(f"Same institution: {pairs(conflicts_.institution)} pair(s); "
          f"{len(conflicts_.institution_index.uncovered)} reviewer(s) with no institutional "
          f"email or affiliation pass unchecked")
    print(f"Topic veto: {pairs(conflicts_.vetoed)} pair(s)")


def relaxed(papers, slates, assigned_via, affinity, reviewers) -> int:
    """List the papers whose slate the repair, weak-pair fix or a hand swap
    changed; return how many."""
    touched = []
    for p in papers:
        pid = p["pid"]
        entries = sorted(((affinity[(e, pid)], e, assigned_via.get((pid, e), "fill"))
                          for e in slates[pid]
                          if assigned_via.get((pid, e), "fill") not in ("fill", "PC anchor")),
                         reverse=True)
        if entries:
            touched.append((p, entries))
    print(f"\n=== Papers changed after the matching ({len(touched)}) ===")
    for p, entries in touched:
        print(f"  [{p['pid']}] {p['title']}")
        for score, e, label in entries:
            print(f"      {label:10s} {score:.3f}  {reviewers[e].name} <{e}>")
    return len(touched)


def reviewer_papers(papers, slates, affinity, reviewers) -> None:
    titles = {p["pid"]: p["title"] for p in papers}
    by_reviewer: dict[str, list[int]] = defaultdict(list)
    for pid, emails in slates.items():
        for e in emails:
            by_reviewer[e].append(pid)
    print("\n=== Reviewers (alphabetical) ===")
    for email in sorted(by_reviewer, key=lambda e: reviewers[e].name.lower()):
        r = reviewers[email]
        pids = sorted(by_reviewer[email], key=lambda pid: -affinity[(email, pid)])
        print(f"\n  {r.name} <{email}>  [{r.pool}]  ({r.primary}) — {len(pids)} paper(s)")
        for pid in pids:
            print(f"      {affinity[(email, pid)]:.3f}  [{pid}] {titles[pid]}")
