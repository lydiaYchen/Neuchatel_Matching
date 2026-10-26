"""The desk-reject check: the three rules, the decisions file and the config."""

import csv
import json

import pytest

from deskreject import check, config

CFG = {"field": "Reserve reviewer", "max_submissions": 2, "margin_over": 5}
PC_TYPE = check.PC_EXEMPT_TYPE + " PC (text as on the form)"


def paper(pid, *emails, reserve="Ann Lee, ann@x.edu", type_="", problems=()):
    return {"pid": pid, "Reserve reviewer": reserve, check.TYPE_FIELD: type_,
            "submission": {"format_problem_fields": list(problems)},
            "authors": [{"given_name": "T", "family_name": e.split("@")[0], "email": e}
                        for e in emails]}


def flagged(tmp_path, papers, formatcheck="paper,messages\n", exempted=()):
    (tmp_path / "p.json").write_text(json.dumps(papers))
    (tmp_path / "a.csv").write_text("paper,email,orcid\n1,a@x.edu,0000-1\n2,a@y.org,0000-1\n")
    (tmp_path / "pc.csv").write_text("given_name,family_name,email,orcid\nP,C,pc@x.edu,\n")
    (tmp_path / "f.csv").write_text(formatcheck)
    check.run(CFG, *(str(tmp_path / n) for n in ("p.json", "a.csv", "pc.csv", "f.csv")),
              set(exempted), tmp_path / "out.csv")
    with open(tmp_path / "out.csv", newline="") as f:
        return {int(r["paper"]): r["reason"] for r in csv.DictReader(f)}


def test_one_author_by_orcid_on_too_many_papers(tmp_path):
    out = flagged(tmp_path, [paper(1, "a@x.edu"), paper(2, "a@y.org"), paper(3, "A@x.edu")])
    assert sorted(out) == [1, 2, 3]
    assert "ORCID 0000-1" in out[1] and "is an author on 3 papers [1, 2, 3]" in out[1]


def test_reserve_reviewer_exemptions(tmp_path):
    out = flagged(tmp_path, [
        paper(1, "pc@x.edu", reserve="", type_=PC_TYPE),       # verified PC author
        paper(2, "b@x.edu", reserve="", type_=PC_TYPE),        # claimed, not on the PC
        paper(3, "c@x.edu", reserve="exempt"),                 # approved
        paper(4, "d@x.edu", reserve="Dee Lee"),                # no email
        paper(5, "e@x.edu", reserve=""),
    ], exempted={3})
    assert sorted(out) == [2, 4, 5]
    assert "claiming a PC member author" in out[2]
    assert "incomplete entry 'Dee Lee'" in out[4]
    assert "no reserve reviewer" in out[5]


def test_format_problems_count_only_if_serious_and_still_there(tmp_path):
    formatcheck = ('paper,messages\n'
                   '1,"Margins too small: text exceeds the block by up to 4%"\n'
                   '2,"Margins too small: text exceeds the block by up to 9%"\n'
                   '3,"Too many pages: 14 pages, limit 12"\n'
                   '4,"Body font too small: 9pt"\n')
    out = flagged(tmp_path, [paper(1, "b@x.edu", problems=["textblock"]),
                             paper(2, "c@x.edu", problems=["textblock"]),
                             paper(3, "d@x.edu", problems=["pagelimit"]),
                             paper(4, "e@x.edu")], formatcheck)        # fixed since
    assert sorted(out) == [2, 3]


def test_decisions_are_the_y_rows(tmp_path):
    path = tmp_path / "d.csv"
    path.write_text("Decision,paper,reason\nY,1,x\nN,2,x\n,3,x\ny,4,by hand\n")
    assert config.load_papers(str(path), decided=True) == {1, 4}
    assert config.load_papers(str(tmp_path / "missing.csv")) == set()


def test_the_deskreject_section_is_validated(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text("name: x\ninputs: {pcinfo: p, authors: a}\n"
                    "deskreject: {papers: d.json, formatcheck: f.csv, max_submissions: 4}\n")
    cfg = config.load(str(path))
    assert (cfg["max_submissions"], cfg["margin_over"], cfg["field"]) == (4, 5,
                                                                          "Reserve reviewer")
    path.write_text("name: x\ninputs: {pcinfo: p, authors: a}\ndeskreject: {papers: d.json}\n")
    with pytest.raises(ValueError, match="formatcheck"):
        config.load(str(path))
