"""What runs after deferred acceptance: filling empty slots, lifting weak
pairs, and the chairs' hand swaps. Each step keeps every conflict layer
(eligibility is the preference lists), every load cap and every class cap.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from collections.abc import Mapping

from .matcher import CappedClasses, resolve_caps


def repair_short_papers(
    pids: list[int],
    prefs: dict[int, list[str]],
    target: dict[int, int],
    slates: dict[int, list[str]],
    used: dict[str, int],
    reviewer_cap: dict[str, int],
    capped: CappedClasses = (),
) -> list[list[tuple[int, str]]]:
    """Fill the slots deferred acceptance left empty by moving reviewers along chains.

    A stable matching need not be complete: once everyone eligible for a paper
    is full of papers they rank higher, it stops. For each missing slot this
    searches breadth first (so the shortest chain) for an eligible reviewer
    with spare capacity, or one who can leave another paper whose slot is
    refilled the same way. Every paper on a chain ends as full as it started.
    Emptiest papers first, pid breaking ties; candidates best first.

    A search state is a paper together with the classes of the reviewer it
    gives up: a paper that gave up a PC member can only take a PC member back,
    while the same paper giving up an extra reviewer can take an extra.
    Returns each chain as [(paper, reviewer it gains), ...], the short paper first.
    """
    limits = resolve_caps(capped, pids)
    holders: dict[str, set[int]] = defaultdict(set)
    for pid in pids:
        for e in slates[pid]:
            holders[e].add(pid)

    def fits(pid: int, slate: list[str], email: str) -> bool:
        return all(sum(1 for e in slate if e in members) + 1 <= limits[k][pid]
                   for k, (members, _) in enumerate(capped) if email in members)

    def classes_of(email: str) -> tuple[int, ...]:
        return tuple(k for k, (members, _) in enumerate(capped) if email in members)

    State = tuple[int, tuple[int, ...]]

    def find_chain(start: int) -> list[tuple[int, str]] | None:
        root: State = (start, ())
        lost: dict[State, str | None] = {root: None}   # the reviewer a paper just gave up
        parent: dict[State, tuple[State, str]] = {}    # state -> (state it gave to, reviewer)
        seen: set[str] = set()
        frontier = [root]
        while frontier:
            nxt = []
            for state in frontier:
                pid, gone = state[0], lost[state]
                slate = [e for e in slates[pid] if e != gone]
                for e in prefs[pid]:
                    if e in seen or e in slate or e == gone or not fits(pid, slate, e):
                        continue
                    if used[e] < reviewer_cap[e]:
                        chain, at, gain = [], state, e
                        while True:
                            chain.append((at[0], gain))
                            if at == root:
                                break
                            at, gain = parent[at]
                        if len({q for q, _ in chain}) == len(chain):
                            return chain[::-1]
                        continue  # passes a paper twice; another state may reach it cleanly
                    seen.add(e)
                    for q in sorted(holders[e]):
                        child = (q, classes_of(e))
                        if q != start and child not in lost:
                            lost[child], parent[child] = e, (state, e)
                            nxt.append(child)
            frontier = nxt
        return None

    chains: list[list[tuple[int, str]]] = []
    for start in sorted(pids, key=lambda pid: (len(slates[pid]), pid)):
        while len(slates[start]) < target[start]:
            chain = find_chain(start)
            if chain is None:
                break
            for i, (pid, e) in enumerate(chain):
                slates[pid].append(e)
                holders[e].add(pid)
                if i + 1 < len(chain):
                    giver = chain[i + 1][0]
                    slates[giver].remove(e)
                    holders[e].discard(giver)
                else:
                    used[e] += 1
            chains.append(chain)
    return chains


def fix_weak_pairs(
    pids: list[int],
    slates: dict[int, list[str]],
    used: dict[str, int],
    reviewer_cap: dict[str, int],
    prefs: dict[int, list[str]],
    affinity: Mapping[tuple[str, int], float],
    capped: CappedClasses,
    below: float,
    assigned_via: dict[tuple[int, str], str],
) -> list[tuple[int, str, int, str, str]]:
    """Move reviewers off weak pairs (similarity below `below`), weakest first.

    For a weak pair (x on p), try every reviewer y on another paper q who
    could take x's place on p: x takes y's place on q (a swap), or a reviewer
    z with spare capacity does. A move is taken only if it creates no weak
    pair, keeps eligibility and every cap, and does not lower the total
    similarity; the best by weakest new pair, then total gain, wins. Each pair
    is tried once. Returns (p, x, q, y, z) per move.
    """
    limits = resolve_caps(capped, pids)
    eligible = {pid: set(prefs[pid]) for pid in pids}

    def weak(e: str, pid: int) -> bool:
        return affinity[(e, pid)] < below

    def fits(pid: int, slate: list[str]) -> bool:
        return all(sum(1 for e in slate if e in members) <= limits[k][pid]
                   for k, (members, _) in enumerate(capped))

    moves: list[tuple[int, str, int, str, str]] = []
    tried: set[tuple[int, str]] = set()
    while True:
        targets = sorted(
            (affinity[(e, pid)], pid, e) for pid in pids for e in slates[pid]
            if weak(e, pid) and (pid, e) not in tried
        )
        if not targets:
            return moves
        _, p, x = targets[0]
        tried.add((p, x))
        rest_p = [e for e in slates[p] if e != x]
        best = None
        for q in pids:
            if q == p:
                continue
            for y in slates[q]:
                if y in rest_p or y not in eligible[p] or weak(y, p) or not fits(p, rest_p + [y]):
                    continue
                rest_q = [e for e in slates[q] if e != y]
                for z in [x] + [e for e in prefs[q] if used[e] < reviewer_cap[e]]:
                    if (z in rest_q or z not in eligible[q] or weak(z, q)
                            or (z != x and z == y) or not fits(q, rest_q + [z])):
                        continue
                    gain = affinity[(y, p)] + affinity[(z, q)] - affinity[(x, p)] - affinity[(y, q)]
                    key = (min(affinity[(y, p)], affinity[(z, q)]), gain, -q, y, z)
                    if gain >= 0 and (best is None or key > best[0]):
                        best = (key, q, y, z, rest_q)
        if best is None:
            continue
        _, q, y, z, rest_q = best
        slates[p] = rest_p + [y]
        slates[q] = rest_q + [z]
        if z != x:
            used[x] -= 1
            used[z] += 1
        for pid, e in ((p, x), (q, y)):
            assigned_via.pop((pid, e), None)
        assigned_via[(p, y)] = assigned_via[(q, z)] = "swap"
        moves.append((p, x, q, y, z))


def apply_swaps(
    path: str,
    slates: dict[int, list[str]],
    used: dict[str, int],
    reviewer_cap: dict[str, int],
    prefs: dict[int, list[str]],
    capped: CappedClasses,
    assigned_via: dict[tuple[int, str], str],
) -> list[tuple[int, str, str]]:
    """The chairs' hand-picked replacements, CSV `paper,out,in,note`, applied last.

    Each row replaces `out` by `in` on the paper (a swap between two papers is
    two rows). Every new pair must be eligible and every cap must hold
    afterwards, else ValueError: a chair's swap is checked like the matcher's
    own pairs. Returns (paper, out, in).
    """
    with open(path, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if (r.get("paper") or "").strip()]
    done, problems = [], []
    for r in rows:
        pid, out, new = int(r["paper"]), r["out"].strip().lower(), r["in"].strip().lower()
        if out not in slates.get(pid, []) or new in slates[pid]:
            problems.append(f"paper {pid}: {out} is not on it, or {new} already is")
            continue
        slates[pid][slates[pid].index(out)] = new
        used[out] -= 1
        used[new] += 1
        assigned_via.pop((pid, out), None)
        assigned_via[(pid, new)] = "hand swap"
        done.append((pid, out, new))
    limits = resolve_caps(capped, list(slates))
    for pid, _, new in done:
        if new not in prefs[pid]:
            problems.append(f"paper {pid}: {new} is not eligible (conflict or topic veto)")
        if any(sum(1 for e in slates[pid] if e in members) > limits[k][pid]
               for k, (members, _) in enumerate(capped)):
            problems.append(f"paper {pid}: a class cap is broken")
        if used[new] > reviewer_cap.get(new, 0):
            problems.append(f"{new} would hold {used[new]} papers, cap {reviewer_cap.get(new, 0)}")
    if problems:
        raise ValueError("; ".join(problems))
    return done
