"""The reserve reviewers: parsing the field, merging, PhD hints, finalising, the config."""

import csv
import json

import pytest

from matching import config as matching_config
from reserve import config, extract, finalise, lookups, parse

PAPER = {"pid": 1, "authors": [
    {"given_name": "Ann", "family_name": "Lee", "email": "ann@x.edu", "affiliation": "X Univ"},
    {"given_name": "Bo", "family_name": "Chan", "email": "bo@y.edu", "affiliation": "Y Univ"}],
    "contacts": []}


def test_a_plain_entry_is_matched_to_its_author():
    row = parse.parse("Dr. Ann Lee, X University, ann@x.edu, https://x.edu/~ann", PAPER)
    assert row == {"firstName": "Ann", "lastName": "Lee", "email": "ann@x.edu",
                   "affiliation": "X University", "website": "https://x.edu/~ann",
                   "emailStatus": "ok"}


def test_an_author_named_with_another_email_gets_their_paper_email():
    row = parse.parse("Bo Chan <bo.chan@gmail.example.com>", PAPER)
    assert (row["email"], row["emailStatus"], row["affiliation"]) == ("bo@y.edu", "modified",
                                                                      "Y Univ")


def test_markdown_tables_are_read():
    text = "| **Name** | Ann Lee |\n| **Email** | ann@x.edu |\n| **Affiliation** | X |"
    assert parse.parse(text, PAPER)["affiliation"] == "X"


def test_exemptions_name_nobody():
    assert parse.parse("Exempt", PAPER) is None
    assert parse.parse("N/A - all authors are on the PC", PAPER) is None
    assert parse.parse("Not applicable, every author is a PhD student", PAPER) is None
    assert parse.skip_reason(" exempt ") == "exempt"
    assert parse.skip_reason("We ask for an exception") == "exemption sentence"


def row(paper, email, status="ok", name="Ann"):
    return {"paper": paper, "firstName": name, "lastName": "Lee", "email": email,
            "affiliation": "", "website": "", "emailStatus": status}


def test_one_person_is_kept_once_by_orcid_or_email():
    rows = [row("1", "ann@x.edu"), row("2", "Ann@X.edu", "modified"),
            row("3", "ann@gmail.example.com"), row("4", "", "unmatched", "Cy"), row("5", "")]
    merged = extract.merge(rows, {"ann@gmail.example.com": "0000-1", "ann@x.edu": "0000-1"})
    assert [(r["paper"], r["emailStatus"]) for r in merged] == [
        ("4", "unmatched"), ("1; 2; 3", "modified"), ("5", "ok")]


def test_phd_hints_come_from_orcid_titles():
    def record(educations=(), jobs=()):
        group = lambda kind, items: {"affiliation-group": [
            {"summaries": [{f"{kind}-summary": i} for i in items]}]}
        return {"activities-summary": {"educations": group("education", educations),
                                       "employments": group("employment", jobs)}}
    done = {"role-title": "PhD", "end-date": {"year": {"value": "2015"}}}
    assert lookups.has_phd(record([done])) == "yes"
    assert lookups.has_phd(record(jobs=[{"role-title": "Assistant Professor"}])) == "yes"
    assert lookups.has_phd(record(jobs=[{"role-title": "PhD student"}])) == "no"
    assert lookups.has_phd(record([{"role-title": "PhD"}])) == "unknown"
    assert lookups.has_phd(None) == "unknown"


def test_finalise_keeps_yes_off_the_pc_with_a_paper_still_in(tmp_path):
    decisions = tmp_path / "d.csv"
    with open(decisions, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Decision", "paper", "firstName", "lastName", "email", "affiliation",
                    "onPCList"])
        w.writerow(["Y", "1; 9", "Ann", "Lee", "Ann@x.edu", "X", "no"])   # 9 desk-rejected
        w.writerow(["Y", "9", "Bo", "Chan", "bo@y.edu", "Y", "no"])       # only paper gone
        w.writerow(["N", "1", "Cy", "Dee", "cy@z.edu", "Z", "no"])
        w.writerow(["Y", "2", "Di", "Ek", "di@w.edu", "W", "yes"])        # on the PC
        w.writerow(["", "2", "Ed", "Fo", "ed@v.edu", "V", "no"])          # undecided
    papers = tmp_path / "p.json"
    papers.write_text(json.dumps([
        {"pid": 1, "authors": [{"email": "ann@x.edu"}]},
        {"pid": 2, "authors": [{"email": "ann@x.edu"}, {"email": "di@w.edu"}]}]))
    authors = tmp_path / "a.csv"
    authors.write_text("paper,email,orcid\n1,ann@x.edu,0000-0002-1825-0097\n")
    assert finalise.run(str(decisions), str(papers), str(authors), tmp_path / "out.csv") == 1
    with open(tmp_path / "out.csv", newline="") as f:
        assert list(csv.reader(f))[1] == ["Ann", "Lee", "Ann@x.edu", "0000-0002-1825-0097",
                                          "X", "2", "1"]


DESK = "deskreject: {papers: dl.json, formatcheck: f.csv}\n"


def test_the_reserve_section_is_validated(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text("name: x\ninputs: {pcinfo: p, extra_pcinfo: e, papers: j, authors: a}\n"
                    "reserve: {decisions: d.csv}\n" + DESK)
    cfg = config.load(str(path))
    assert (cfg["papers"], cfg["deadline_papers"], cfg["desk_rejects"], cfg["field"]) == (
        "j", "dl.json", "curated/desk_rejects.csv", "Reserve reviewer")
    matching_config.load(str(path))         # the matching accepts the section
    path.write_text("name: x\ninputs: {pcinfo: p, papers: j, authors: a}\n"
                    "reserve: {mapper: m}\n" + DESK)
    with pytest.raises(ValueError, match="unknown key"):
        config.load(str(path))


def test_a_desk_rejected_paper_takes_its_reserve_reviewer_and_pc_member_along(tmp_path):
    def paper(pid, field, *emails):
        return {"pid": pid, "Reserve reviewer": field, "contacts": [],
                "authors": [{"given_name": e.split("@")[0].title(), "family_name": "Lee",
                             "email": e} for e in emails]}
    papers = tmp_path / "p.json"
    papers.write_text(json.dumps([
        paper(1, "Ann Lee, ann@x.edu", "ann@x.edu"),
        paper(2, "Ann Lee, ann@x.edu", "ann@x.edu"),          # desk-rejected
        paper(3, "Bo Lee, bo@x.edu", "bo@x.edu"),
        paper(4, "Cy Lee, cy@x.edu", "cy@x.edu"),            # desk-rejected, Cy on the PC
    ]))
    authors = tmp_path / "a.csv"
    authors.write_text("paper,email,orcid\n")
    pcinfo = tmp_path / "pc.csv"
    pcinfo.write_text("given_name,family_name,email,orcid\nCy,Lee,CY@x.edu,\n")
    out, pc = tmp_path / "out.csv", tmp_path / "pc_out.csv"
    assert extract.run(str(papers), "Reserve reviewer", {2, 4}, str(authors), str(pcinfo),
                       tmp_path / "cache", out, pc) == 1
    with open(out, newline="") as f:
        assert [(r["paper"], r["email"]) for r in csv.DictReader(f)] == [("3", "bo@x.edu")]
    with open(pc, newline="") as f:
        assert list(csv.reader(f))[1:] == [["Cy", "Lee", "CY@x.edu", "4"]]
