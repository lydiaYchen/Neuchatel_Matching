"""The TPMS files: renamed papers, PC conflicts, reviewer info and the config."""

import csv
import zipfile

import pytest

from matching import config as matching_config
from tpms import config, conflicts, emails, papers, pipeline, reviewers

from .helpers import account, write_export

FALL = [{"tag": "fall0", "min": 0, "max": 0}]
DEFAULT = {"min": 4, "max": 5}


def read(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.reader(f))


def test_papers_are_renamed_and_kept_byte_for_byte(tmp_path):
    src = tmp_path / "in.zip"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("eurosys27-fall-paper7.pdf", b"seven")
        z.writestr("eurosys27-fall-paper12.pdf", b"twelve")
    assert papers.rename(str(src), tmp_path / "out.zip") == 2
    with zipfile.ZipFile(tmp_path / "out.zip") as z:
        assert z.namelist() == ["paper7.pdf", "paper12.pdf"]
        assert z.read("paper12.pdf") == b"twelve"


def test_a_zip_entry_that_is_not_a_paper_is_refused(tmp_path):
    src = tmp_path / "in.zip"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("notes.txt", b"")
    with pytest.raises(ValueError, match="not a paper<id>.pdf"):
        papers.rename(str(src), tmp_path / "out.zip")


def test_email_lists_are_optional_and_skip_blanks(tmp_path):
    assert emails.load_chairs(str(tmp_path / "missing.csv")) == set()
    path = tmp_path / "changes.csv"
    path.write_text("old_email,new_email\na@x.edu,a@y.org\nb@x.edu,\n")
    assert emails.load_email_changes(str(path)) == {"a@x.edu": "a@y.org"}


def test_conflicts_change_emails_and_drop_the_chairs(tmp_path):
    src = tmp_path / "pcconflicts.csv"
    src.write_text("paper,title,given_name,family_name,email,conflicttype\n"
                   "1,T,A,A,a@x.edu,Collaborator\n"
                   "1,T,C,C,chair@x.edu,Pinned conflict\n"
                   "2,U,B,B,b@x.edu,Author\n")
    conflicts.write(str(src), tmp_path / "out.csv", chairs={"chair@x.edu"},
                    changes={"a@x.edu": "a@y.org"})
    assert read(tmp_path / "out.csv") == [["paperID", "reviewerEmail"],
                                          ["1", "a@y.org"], ["2", "b@x.edu"]]


def test_limits_take_the_first_rule_and_chairs_get_none():
    rules = [{"tag": "tpmsnopdf", "leave_out": True}, {"tag": "spring0.5", "min": 3, "max": 4}]
    assert reviewers.limits({"spring0.5"}, chair=False, rules=rules, default=DEFAULT) == (3, 4)
    assert reviewers.limits({"tpmsnopdf", "spring0.5"}, chair=False, rules=rules,
                            default=DEFAULT) is None
    assert reviewers.limits({"spring0"}, chair=False, rules=rules, default=DEFAULT) == (4, 5)
    assert reviewers.limits({"spring0.5"}, chair=True, rules=rules, default=DEFAULT) == (0, 0)


def test_reviewer_info_follows_the_export(tmp_path):
    src = write_export(tmp_path / "pcinfo.csv", [
        account("a@x.edu"), account("b@x.edu", tags="~~fall0#1"), account("chair@x.edu")])
    reviewers.write(src, tmp_path / "out.csv", chairs={"chair@x.edu"},
                    changes={"a@x.edu": "a@y.org"}, rules=FALL, default=DEFAULT)
    assert read(tmp_path / "out.csv") == [
        ["Email", "FirstName", "LastName", "min", "max"],
        ["a@y.org", "Test", "A", "4", "5"],
        ["b@x.edu", "Test", "B", "0", "0"],
        ["chair@x.edu", "Test", "Chair", "0", "0"],
    ]


def write_config(tmp_path, tpms: str):
    path = tmp_path / "c.yaml"
    path.write_text("name: x\ninputs: {pcinfo: p, extra_pcinfo: e, papers: j, authors: a}\n"
                    "tpms:\n  papers: z\n  pcconflicts: c\n" + tpms)
    return str(path)


def test_the_tpms_section_is_validated(tmp_path):
    good = write_config(tmp_path, "  limits: [{tag: fall0, min: 0, max: 0}]\n"
                                  "  default_limits: {min: 4, max: 5}\n")
    cfg = config.load(good)
    assert (cfg["name"], cfg["pcinfo"], cfg["limits"]) == ("x", "p", FALL)
    matching_config.load(good)              # the matching accepts the section
    with pytest.raises(ValueError, match="unknown key"):
        config.load(write_config(tmp_path, "  default_limits: {min: 4, max: 5}\n  cap: 3\n"))
    with pytest.raises(ValueError, match="0 <= min <= max"):
        config.load(write_config(tmp_path, "  default_limits: {min: 5, max: 4}\n"))
    with pytest.raises(ValueError, match="leave_out"):
        config.load(write_config(tmp_path, "  limits: [{tag: fall0, min: 0}]\n"
                                           "  default_limits: {min: 4, max: 5}\n"))


def test_extra_reviewers_all_get_the_extra_limits(tmp_path):
    src = tmp_path / "final.csv"
    src.write_text("firstName,lastName,email,orcid,affiliation,numSubmittedPapers,"
                   "numReserveNominations\nAnn,Lee, ann@x.edu ,,X,1,1\nBo,Chan,bo@y.edu,,Y,2,1\n")
    written = reviewers.write_extra(str(src), tmp_path / "out.csv",
                                    changes={"bo@y.edu": "bo@z.org"}, limits={"min": 5, "max": 6})
    assert written == {"ann@x.edu", "bo@z.org"}
    assert read(tmp_path / "out.csv")[1:] == [["ann@x.edu", "Ann", "Lee", "5", "6"],
                                              ["bo@z.org", "Bo", "Chan", "5", "6"]]


def test_conflicts_of_unknown_addresses_are_reported(capsys):
    pipeline.check_emails({"Ann@x.edu", "cy@x.edu"}, {"ann@x.edu"}, "extra_pcconflicts.csv")
    err = capsys.readouterr().err
    assert "1 address(es)" in err and "cy@x.edu" in err


def test_extra_limits_are_needed_only_with_extra_conflicts(tmp_path):
    base = "  default_limits: {min: 4, max: 5}\n"
    assert config.load(write_config(tmp_path, base))["extra_pcconflicts"] is None
    with pytest.raises(ValueError, match="extra_limits"):
        config.load(write_config(tmp_path, base + "  extra_pcconflicts: e.csv\n"))
