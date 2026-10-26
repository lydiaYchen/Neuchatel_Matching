"""The conflict uploads: HotCRP's conflict page, institution matching, the config."""

import csv
import json

import pytest

from conflicts import affiliations, config, institutions, pages

RADIOS = ('<input type="radio" class="btn cflt" name="a" value="conflict" title="Conflict">'
          '<input type="radio" class="btn cflt" name="a" value="nonconflict" {} title="No">')


def heading(name):
    return (f'<tr class="plheading"><td class="plheading" colspan="4">{name} '
            f'<span class="auaff">(Some University)</span></td></tr>')


def row(pid, checked="nonconflict"):
    radios = RADIOS.format('checked=""' if checked == "nonconflict" else "")
    if checked == "conflict":
        radios = radios.replace('value="conflict"', 'value="conflict" checked=""')
    return f'<tr class="pl k0" data-pid="{pid}"><td class="pl">#{pid}</td><td>{radios}</td></tr>'


def read(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.reader(f))


def test_the_conflict_page_becomes_an_upload(tmp_path):
    page = tmp_path / "page.html"
    page.write_text("<table>" + heading("Ann Lee") + row(3) + row(5, "conflict") + row(7)
                    + heading("Wei Chen &lt;wei.chen@lab.example.org&gt;") + row(3)
                    + heading("Nobody Known") + row(9) + "</table>")
    export = tmp_path / "pc.csv"
    export.write_text("given_name,family_name,email,affiliation\n"
                      "Ann,Lee,ann@x.edu,X\nWei,Chen,wei@x.edu,X\n"
                      "Wei,Chen,wei.chen@lab.example.org,Lab\n")
    assert pages.run(str(page), str(export), tmp_path / "out.csv") == 3
    assert read(tmp_path / "out.csv") == [
        ["paper", "action", "email", "conflict"], ["3", "conflict", "ann@x.edu", "yes"],
        ["7", "conflict", "ann@x.edu", "yes"],
        ["3", "conflict", "wei.chen@lab.example.org", "yes"]]


def test_a_page_with_no_reviewer_is_refused(tmp_path):
    page = tmp_path / "page.html"
    page.write_text("<html>Sign in</html>")
    export = tmp_path / "pc.csv"
    export.write_text("given_name,family_name,email,affiliation\n")
    with pytest.raises(ValueError, match="no reviewer on the page"):
        pages.run(str(page), str(export), tmp_path / "out.csv")


def test_email_domains_keep_the_institution():
    assert institutions.email_domain("a@cs.vt.edu") == "vt.edu"
    assert institutions.email_domain("a@ict.ac.cn") == "ict.ac.cn"
    assert institutions.email_domain("a@gmail.com") == ""


def test_affiliation_terms_match_whole_institutions():
    terms = institutions.search_terms("Tsinghua University", {})
    assert terms == ["tsinghua university", "tsinghua"]
    nanjing = institutions.term_pattern("nanjing university")
    assert nanjing.search("nanjing university")
    assert not nanjing.search("nanjing university of science and technology")
    hku = institutions.term_pattern("university of hong kong")
    assert not hku.search("chinese university of hong kong")
    combined = {"NVIDIA & Stanford": ["nvidia", "stanford university"]}
    assert institutions.search_terms("NVIDIA & Stanford", combined) == ["nvidia",
                                                                       "stanford university"]


def test_affiliation_conflicts_skip_what_hotcrp_has(tmp_path):
    export = tmp_path / "extra.csv"
    export.write_text("given_name,family_name,email,affiliation\n"
                      "Ann,Lee,ann@cs.x.edu,X University\nBo,Chan,bo@gmail.com,Y Corp\n")
    pcconflicts = tmp_path / "pcc.csv"
    pcconflicts.write_text("paper,title,given_name,family_name,email,conflicttype\n"
                           "1,T,Ann,Lee,ANN@cs.x.edu,Conflict\n")
    papers = tmp_path / "p.json"
    papers.write_text(json.dumps([
        {"pid": 1, "authors": [{"email": "cy@x.edu", "affiliation": "X University"}]},
        {"pid": 2, "authors": [{"email": "di@mail.x.edu", "affiliation": "Elsewhere"}]},
        {"pid": 3, "authors": [{"email": "ed@y.com", "affiliation": "Z Univ"}]}]))
    out, evidence = tmp_path / "out.csv", tmp_path / "ev.csv"
    assert affiliations.run(str(export), str(pcconflicts), str(papers), {}, out, evidence) == 1
    assert read(out)[1:] == [["2", "conflict", "ann@cs.x.edu", "yes"]]
    assert read(evidence)[1:] == [["1", "ann@cs.x.edu", "yes", "yes", "yes"],
                                  ["2", "ann@cs.x.edu", "no", "yes", "no"]]


def test_the_conflicts_section_is_validated(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text("name: x\ninputs: {pcinfo: p}\nconflicts: {pc_page: pc.html}\n")
    assert config.load(str(path))["pc_page"] == "pc.html"
    path.write_text("name: x\ninputs: {pcinfo: p}\nconflicts: {extra_pcconflicts: e.csv}\n")
    with pytest.raises(ValueError, match="inputs.extra_pcinfo, inputs.papers"):
        config.load(str(path))
    path.write_text("name: x\nconflicts: {page: p.html}\n")
    with pytest.raises(ValueError, match="unknown key"):
        config.load(str(path))
