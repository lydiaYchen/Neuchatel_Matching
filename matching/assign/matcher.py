"""Paper-proposing deferred acceptance under per-paper class caps.

The "hospitals/residents" generalisation of Gale-Shapley (the algorithm of
the US medical residency match): each paper proposes to its eligible
reviewers in order of similarity; a reviewer holds the best offers so far,
up to their load cap, bumping a worse one when a better one arrives. The
result has no "blocking pair" (a reviewer and a paper who would both rather
have each other than one of their current matches), checked afterwards by
`count_blocking_pairs`.

A class cap (`capped`) limits how many members of a reviewer class one paper
may hold: the extra reviewers, the PC, the reviewers from each country. Caps
are hard, so a paper under-fills rather than exceed one. A member a paper
cannot take yet is deferred, not rejected, and offered the slot if one opens.
"""

from __future__ import annotations

import sys
from collections import defaultdict, deque
from collections.abc import Mapping, Sequence

# A limit that names no paper binds none of them.
UNCAPPED = sys.maxsize

# (members, limit): one int for every paper, or {pid: limit} for the papers it names.
ClassLimit = int | Mapping[int, int]
CappedClasses = Sequence[tuple[frozenset[str], ClassLimit]]


def resolve_caps(capped: CappedClasses, pids) -> list[dict[int, int]]:
    """Per-class, per-paper limits."""
    resolved = []
    for _, limit in capped:
        if isinstance(limit, int):
            resolved.append({pid: limit for pid in pids})
        else:
            resolved.append({pid: limit.get(pid, UNCAPPED) for pid in pids})
    return resolved


def class_counts_of(
    slates: dict[int, list[str]], pids, capped: CappedClasses
) -> dict[int, list[int]]:
    """Per-paper counts of each capped class already held."""
    return {
        pid: [sum(1 for e in slates[pid] if e in emails) for emails, _ in capped]
        for pid in pids
    }


def deferred_acceptance(
    pids: list[int],
    paper_prefs: dict[int, list[str]],
    paper_target: dict[int, int],
    reviewer_cap: dict[str, int],
    score: dict[tuple[str, int], float],
    capped: CappedClasses = (),
    held_counts: dict[int, list[int]] | None = None,
) -> dict[int, list[str]]:
    """{pid: [email, ...]}: each paper up to its target from its preference list.

    `paper_prefs[pid]` is sorted by descending score. `held_counts` seeds each
    paper's class counts with what earlier phases froze, so caps accumulate
    across phases. Deferred candidates wait in queues keyed by the exact set of
    classes they belong to, so a queue's head is blocked only when the whole
    queue is.
    """
    limits = resolve_caps(capped, pids)
    sig_of: dict[str, tuple[int, ...]] = {}
    for k, (emails, _) in enumerate(capped):
        for email in emails:
            sig_of[email] = sig_of.get(email, ()) + (k,)
    cells = sorted(set(sig_of.values()))
    cell_index = {sig: i for i, sig in enumerate(cells)}

    paper_ptr = {pid: 0 for pid in pids}
    paper_held: dict[int, list[str]] = {pid: [] for pid in pids}
    held_counts = held_counts or {}
    class_held = {pid: list(held_counts.get(pid, [0] * len(capped))) for pid in pids}
    deferred: dict[int, dict[int, deque[str]]] = {pid: {} for pid in pids}
    reviewer_held: dict[str, list[tuple[int, float]]] = {}

    def sig_ok(pid: int, sig: tuple[int, ...]) -> bool:
        return all(class_held[pid][k] < limits[k][pid] for k in sig)

    def cell_ok(pid: int, c: int) -> bool:
        return sig_ok(pid, cells[c])

    def proposable(pid: int) -> bool:
        return paper_ptr[pid] < len(paper_prefs[pid]) or any(
            cell_ok(pid, c) for c in deferred[pid]
        )

    def count_class(pid: int, email: str, delta: int) -> None:
        for k in sig_of.get(email, ()):
            class_held[pid][k] += delta

    def next_candidate(pid: int) -> str | None:
        """Best-scoring candidate the paper can take now: the preference-list
        head, or the head of a deferred queue whose classes have room."""
        prefs = paper_prefs[pid]
        while paper_ptr[pid] < len(prefs):
            sig = sig_of.get(prefs[paper_ptr[pid]])
            if sig is None or sig_ok(pid, sig):
                break
            cell = deferred[pid].setdefault(cell_index[sig], deque())
            cell.append(prefs[paper_ptr[pid]])
            paper_ptr[pid] += 1
        head = prefs[paper_ptr[pid]] if paper_ptr[pid] < len(prefs) else None
        best, best_c = head, None
        for c, dq in deferred[pid].items():
            if cell_ok(pid, c):
                if best is None or score[(dq[0], pid)] >= score[(best, pid)]:
                    best, best_c = dq[0], c
        if best_c is not None:
            dq = deferred[pid][best_c]
            email = dq.popleft()
            if not dq:
                del deferred[pid][best_c]
            return email
        if head is not None:
            paper_ptr[pid] += 1
        return head

    queue = deque(pid for pid in pids if paper_target[pid] > 0 and paper_prefs[pid])
    while queue:
        pid = queue.popleft()
        if len(paper_held[pid]) >= paper_target[pid]:
            continue
        email = next_candidate(pid)
        if email is None:
            continue  # re-queued by the bump that frees a class slot, if any

        s = score[(email, pid)]
        held = reviewer_held.setdefault(email, [])
        bumped_pid = None
        if len(held) < reviewer_cap[email]:
            held.append((pid, s))
            paper_held[pid].append(email)
            count_class(pid, email, +1)
        else:
            worst_i = min(range(len(held)), key=lambda i: held[i][1])
            worst_pid, worst_score = held[worst_i]
            if s > worst_score:
                held[worst_i] = (pid, s)
                paper_held[pid].append(email)
                count_class(pid, email, +1)
                paper_held[worst_pid].remove(email)
                count_class(worst_pid, email, -1)
                bumped_pid = worst_pid

        if bumped_pid is not None:
            queue.append(bumped_pid)
        if len(paper_held[pid]) < paper_target[pid] and proposable(pid):
            queue.append(pid)

    return paper_held


def count_blocking_pairs(
    eligible_by_pid: dict[int, list[tuple[str, float]]],
    paper_held: dict[int, list[str]],
    reviewer_cap: dict[str, int],
    paper_target: dict[int, int],
    score: dict[tuple[str, int], float],
    capped: CappedClasses = (),
    held_counts: dict[int, list[int]] | None = None,
) -> int:
    """Pairs that would both rather have each other: a self-check on
    `deferred_acceptance`, which must find 0.

    Zero is guaranteed only when the classes are pairwise disjoint or nested.
    A tier class (PC, extra) crosses the country classes, so callers judge the
    papers carrying both apart.
    """
    pids = list(eligible_by_pid)
    limits = resolve_caps(capped, pids)
    sig_of: dict[str, tuple[int, ...]] = {}
    for k, (emails, _) in enumerate(capped):
        for email in emails:
            sig_of[email] = sig_of.get(email, ()) + (k,)
    held_counts = held_counts or {}
    reviewer_papers: dict[str, list[int]] = defaultdict(list)
    for pid, emails in paper_held.items():
        for email in emails:
            reviewer_papers[email].append(pid)

    blocking = 0
    for pid, pairs in eligible_by_pid.items():
        held = set(paper_held[pid])
        seed = held_counts.get(pid) or [0] * len(capped)
        class_counts = [
            seed[k] + sum(1 for e in paper_held[pid] if e in emails)
            for k, (emails, _) in enumerate(capped)
        ]
        for email, s in pairs:
            if email in held:
                continue
            my_papers = reviewer_papers.get(email, [])
            reviewer_wants = len(my_papers) < reviewer_cap[email] or any(
                score[(email, p2)] < s for p2 in my_papers
            )
            if not reviewer_wants:
                continue
            current = paper_held[pid]
            full = [k for k in sig_of.get(email, ()) if class_counts[k] >= limits[k][pid]]
            if full:
                paper_wants = any(
                    score[(r2, pid)] < s and all(r2 in capped[k][0] for k in full)
                    for r2 in current
                )
            else:
                paper_wants = len(current) < paper_target[pid] or any(
                    score[(r2, pid)] < s for r2 in current
                )
            if paper_wants:
                blocking += 1
    return blocking


def assignment_phase(
    pids: list[int],
    full_prefs: dict[int, list[str]],
    phase_target: dict[int, int],
    slates: dict[int, list[str]],
    used: dict[str, int],
    reviewer_cap: dict[str, int],
    score: dict[tuple[str, int], float],
    candidates: frozenset[str] | set[str],
    capped: CappedClasses = (),
):
    """One deferred-acceptance pass over `candidates`, on top of the slates so far.

    `phase_target[pid]` is how many more reviewers the paper may gain; what
    earlier phases placed is frozen but still counts against the caps. Folds
    the result into `slates` and `used`; returns (held, prefs, cap) for the
    self-check.
    """
    cap = {}
    for email in candidates:
        remaining = reviewer_cap[email] - used[email]
        if remaining > 0:
            cap[email] = remaining
    prefs = {}
    for pid in pids:
        taken = set(slates[pid])
        prefs[pid] = [e for e in full_prefs[pid] if e in cap and e not in taken]
    held_counts = class_counts_of(slates, pids, capped)

    held = deferred_acceptance(pids, prefs, phase_target, cap, score, capped, held_counts)
    for pid, emails in held.items():
        slates[pid].extend(emails)
        for e in emails:
            used[e] += 1
    return held, prefs, cap
