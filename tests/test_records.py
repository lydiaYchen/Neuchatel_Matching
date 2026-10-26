"""The per-reviewer records joined from a run directory."""

import csv
import json

from matching import records
from matching.config import RunDir


def make_run(root):
    rd = RunDir(root)

    def entry(name, pool, pubs):
        return {"name": name, "pool": pool, "orcid": "0000", "dblp": "1/1", "dblp_name": name,
                "placed_by": "person", "basis": "dblp" if pubs else "topics", "years": 4,
                "areas": ["Storage"], "keywords": "Networks", "publications": pubs}
    rd.basis["pc"].write_text(json.dumps({"a@x.edu": entry(
        "Ann", "pc", [[2024, "Older", "", ""], [2025, "Newer", "10.1/n", "openalex"]])}))
    rd.basis["extra"].write_text(json.dumps({"b@x.edu": entry("Bo", "extra", [])}))
    rd.conflicts.write_text("pid,email,layer,author,evidence\n"
                            "1,a@x.edu,declared,,collaborator\n"
                            "1,a@x.edu,coauthor,Cy,2 paper(s)\n"
                            "5,a@x.edu,topic-veto,,Networks (-2)\n")
    rd.upload.write_text("paper,action,email,round\nall,clearreview,all,R1\n"
                         "2,primaryreview,b@x.edu,R1\n3,primaryreview,B@x.edu,R1\n")
    return rd


def test_records_join_basis_conflicts_and_assignment(tmp_path):
    rd = make_run(tmp_path)
    records.write(rd)
    rows = {r["email"]: r for r in csv.DictReader(open(rd.reviewers))}
    a, b = rows["a@x.edu"], rows["b@x.edu"]
    assert (a["pool"], a["papers"], a["with_abstract"], a["conflicted_papers"], a["coauthor"],
            a["topic_veto"], a["assigned"]) == ("pc", "2", "1", "1", "1", "1", "0")
    assert (b["pool"], b["papers"], b["assigned"]) == ("extra", "0", "2")
    pubs = [(p["email"], p["year"], p["title"]) for p in csv.DictReader(open(rd.publications))]
    assert pubs == [("a@x.edu", "2025", "Newer"), ("a@x.edu", "2024", "Older")]


def test_one_record_is_printed(tmp_path, capsys):
    records.show(make_run(tmp_path), "a@x.edu")
    out = capsys.readouterr().out
    assert "Ann <a@x.edu>, PC" in out
    assert "2025  Newer  doi:10.1/n" in out and "2024  Older  [title only]" in out
    assert "Conflicts of interest: 1 paper(s)" in out and "#1  coauthor  Cy: 2 paper(s)" in out
    assert "Topic veto (a topic they rated negatively): 1 paper(s)" in out
