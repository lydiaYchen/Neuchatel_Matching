"""The conflict layers, and the itemised conflicts file agreeing with them."""

from matching import coi, hotcrp
from matching.coi.coauthors import CoauthorIndex, build_coauthor_index, coauthor_conflicts
from matching.coi.collaborators import collaborator_conflicts
from matching.coi.institutions import (
    affiliation_keys, build_institution_index, institution_conflicts, org_domain,
)

from .helpers import author, paper, reviewer


def test_org_domain_and_affiliation_keys():
    assert org_domain("x@mails.tsinghua.edu.cn") == org_domain("y@cs.tsinghua.edu.cn") == "tsinghua.edu.cn"
    assert org_domain("x@gmail.com") == org_domain("x@alumni.ethz.ch") == ""
    assert affiliation_keys("University of Washington") != affiliation_keys("Washington University")
    assert affiliation_keys("Huawei Technologies Co., Ltd.") == affiliation_keys("Huawei")
    assert affiliation_keys("Independent Researcher") == set()


def test_coauthors_by_dblp_page_beat_namesakes():
    a = reviewer("a@x.edu", pid="orcid:A")
    b = reviewer("b@x.edu", pid="orcid:B")
    this_year = 2026
    names = {"orcid:A": {"Wei Zhang": [[this_year, "Ours"]]},
             "orcid:B": {"Wei Zhang": [[this_year, "Theirs"]]}}
    pages = {"orcid:A": {"1/1": [[this_year, "Ours"]]}, "orcid:B": {"2/2": [[this_year, "Theirs"]]}}
    index = build_coauthor_index([a, b], names, pages, {}, years=50)
    found = coauthor_conflicts([paper(1, authors=[author("wz@y.edu", "Wei Zhang")])], index,
                               {"wz@y.edu": "1/1"})
    assert {e: c.match for e, c in found[1].items()} == {"a@x.edu": "dblp"}
    by_name = coauthor_conflicts([paper(1, authors=[author("wz@y.edu", "Wei Zhang")])], index, {})
    assert set(by_name[1]) == {"a@x.edu", "b@x.edu"}


def layers():
    revs = [
        reviewer("a@a.edu", hotcrp_email="a.alias@a.edu"),        # declared, other address
        reviewer("b@b.edu"),                                       # author
        reviewer("d@y.org", name="Dee Collab"),                    # named by the paper
        reviewer("e@z.edu"),                                       # same institution
        reviewer("f@w.org", disliked=[("Storage", -2)]),
        reviewer("g@v.org", disliked=[("Storage", -1)]),          # above the -2 veto
        reviewer("h@u.org"),
    ]
    papers = [
        paper(1, authors=[author("b@b.edu")], pc_conflicts={"A.Alias@a.edu": "collaborator"},
              collaborators="Dee Collab (Somewhere)"),
        paper(2, topics=["Networks"], authors=[author("q@z.edu", "Bo Author")]),
    ]
    accounts = hotcrp.Accounts([])
    index = build_institution_index(revs, accounts)
    conflicts = coi.Conflicts(
        coauthor={}, collaborator=collaborator_conflicts(papers, revs, accounts),
        institution=institution_conflicts(papers, index),
        vetoed=coi.topic_veto(papers, revs, -2), coauthor_index=CoauthorIndex(),
        institution_index=index, author_pids={}, author_unidentified={})
    return papers, {r.email: r for r in revs}, conflicts


def test_every_excluded_pair_is_listed_with_its_layer():
    papers, revs, conflicts = layers()
    rows = coi.conflict_rows(papers, list(revs), revs, conflicts, -2)
    assert rows == [
        (1, "a@a.edu", "declared", "", "collaborator"),
        (1, "b@b.edu", "own-paper", "", "author"),
        (1, "b@b.edu", "institution", "Ann Author", "b.edu (email domain)"),
        (1, "d@y.org", "collaborator", "", "collab dee (paper declared)"),
        (1, "f@w.org", "topic-veto", "", "Storage (-2)"),
        (2, "e@z.edu", "institution", "Bo Author", "z.edu (email domain)"),
    ]
    def eligible():
        return {p["pid"]: [e for e, r in revs.items()
                           if not {e, r.hotcrp_email.lower()} & conflicts.excluded(p)]
                for p in papers}
    assert coi.unexplained_exclusions(rows, list(revs), eligible()) == (0, 0)
    conflicts.vetoed = {}      # a layer the matcher no longer applies, still listed
    assert coi.unexplained_exclusions(rows, list(revs), eligible()) == (0, 1)
