"""The matcher: deferred acceptance under caps, repair, weak pairs, swaps, countries."""

from collections import defaultdict

import pytest

from matching.assign import countries, extra_submission_caps
from matching.assign.matcher import assignment_phase, count_blocking_pairs, deferred_acceptance
from matching.assign.repair import apply_swaps, fix_weak_pairs, repair_short_papers
from matching.roster import EXTRA

from .helpers import author, paper, reviewer


def test_deferred_acceptance_gives_the_contested_reviewer_to_the_better_match():
    score = {("r1", 1): 0.9, ("r1", 2): 0.8, ("r2", 1): 0.5, ("r2", 2): 0.7}
    prefs = {1: ["r1", "r2"], 2: ["r1", "r2"]}
    held = deferred_acceptance([1, 2], prefs, {1: 1, 2: 1}, {"r1": 1, "r2": 1}, score)
    assert held == {1: ["r1"], 2: ["r2"]}
    pairs = {pid: [(e, score[(e, pid)]) for e in prefs[pid]] for pid in prefs}
    assert count_blocking_pairs(pairs, held, {"r1": 1, "r2": 1}, {1: 1, 2: 1}, score) == 0


def test_a_class_cap_defers_rather_than_exceeds():
    score = {(e, 1): s for e, s in (("x1", 0.9), ("x2", 0.8), ("p", 0.1))}
    held = deferred_acceptance([1], {1: ["x1", "x2", "p"]}, {1: 2}, {"x1": 1, "x2": 1, "p": 1},
                               score, capped=[(frozenset({"x1", "x2"}), 1)])
    assert held == {1: ["x1", "p"]}


def test_phases_freeze_what_earlier_phases_placed():
    score = {("pc", 1): 0.2, ("x", 1): 0.9}
    slates, used = {1: []}, defaultdict(int)
    cap = {"pc": 1, "x": 1}
    assignment_phase([1], {1: ["x", "pc"]}, {1: 1}, slates, used, cap, score, {"pc"})
    assignment_phase([1], {1: ["x", "pc"]}, {1: 1}, slates, used, cap, score, {"pc", "x"})
    assert slates == {1: ["pc", "x"]}


def test_repair_moves_a_reviewer_along_a_chain():
    # Paper 2 can only take r1, who is on paper 1; r2 has room and fits paper 1.
    slates = {1: ["r1"], 2: []}
    used = defaultdict(int, {"r1": 1})
    chains = repair_short_papers([1, 2], {1: ["r1", "r2"], 2: ["r1"]}, {1: 1, 2: 1}, slates, used,
                                 {"r1": 1, "r2": 1})
    assert chains == [[(2, "r1"), (1, "r2")]]
    assert slates == {1: ["r2"], 2: ["r1"]}


def test_weak_pair_fix_swaps_only_when_nothing_gets_weak():
    affinity = {("x", 1): 0.80, ("y", 1): 0.95, ("x", 2): 0.96, ("y", 2): 0.97}
    slates, via = {1: ["x"], 2: ["y"]}, {}
    moves = fix_weak_pairs([1, 2], slates, defaultdict(int, {"x": 1, "y": 1}), {"x": 1, "y": 1},
                           {1: ["x", "y"], 2: ["x", "y"]}, affinity, [], 0.9, via)
    assert moves == [(1, "x", 2, "y", "x")]
    assert slates == {1: ["y"], 2: ["x"]}


def test_hand_swaps_are_checked_like_the_matchers_own(tmp_path):
    path = tmp_path / "s.csv"
    path.write_text("paper,out,in,note\n1,a,c,\n")
    slates, used = {1: ["a", "b"]}, defaultdict(int, {"a": 1, "b": 1})
    with pytest.raises(ValueError, match="not eligible"):
        apply_swaps(str(path), slates, used, {"a": 1, "b": 1, "c": 1}, {1: ["a", "b"]}, [], {})


def test_extra_reviewers_authoring_submissions_take_a_larger_cap():
    rs = [reviewer("x@a.edu", pool=EXTRA), reviewer("y@a.edu", pool=EXTRA, cap=3),
          reviewer("z@a.edu", pool=EXTRA, pid="orcid:0000-0002-1825-0097")]
    papers = [paper(1, authors=[author("x@a.edu"), author("y@a.edu")]),
              paper(2, authors=[author("x@a.edu")]), paper(3, authors=[author("q@b.edu")])]
    caps = extra_submission_caps(rs, papers, {2: 7, 3: 8}, {3: {"0000-0002-1825-0097"}, 2: {"0000-0002-1825-0097"}})
    assert caps == {"x@a.edu": 7, "z@a.edu": 7}


def test_countries_regions_and_generic_domains():
    assert countries.from_text("Hong Kong University of Science and Technology, China") == "HK"
    assert countries.from_text("Indiana University") == ""
    assert countries.from_email("a@cs.tsinghua.edu.cn") == "CN"
    assert countries.from_email("a@mit.edu") == countries.from_email("a@x.io") == ""
    assert countries.country("Anywhere", "a@x.cn", "US") == "US"


def test_a_paper_is_capped_on_its_majority_country():
    rs = {"cn@a.cn": reviewer("cn@a.cn"), "us@b.edu": reviewer("us@b.edu", affiliation="MIT, USA")}
    papers = [paper(1, authors=[author("a@x.cn"), author("b@y.cn"), author("c@z.de")]),
              paper(2, authors=[author("a@x.com")])]
    caps, reviewer_country, thin = countries.country_caps(papers, list(rs), rs, {}, 1)
    assert [(c.code, set(c.members), c.papers) for c in caps] == [("CN", {"cn@a.cn"}, {1: 1})]
    assert reviewer_country == {"cn@a.cn": "CN", "us@b.edu": "US"}
    assert thin == [2]
