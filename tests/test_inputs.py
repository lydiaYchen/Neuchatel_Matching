"""Names, HotCRP files, rosters and the config."""

import csv

import pytest

from matching import config, hotcrp, roster
from matching.names import names_agree, names_match, token_set

from .helpers import account, write_export


def test_names_fold_order_initials_and_nicknames():
    assert token_set("José A. García") == token_set("Garcia Jose")
    assert token_set("Rich Smith") == token_set("Richard Smith")
    assert token_set("Wei Zhang 0012") == token_set("Wei Zhang")


def test_names_match_needs_two_shared_words():
    assert names_match(token_set("David Albert Wood"), token_set("David Wood")) == "partial"
    assert names_match(token_set("David Wood"), token_set("Wood David")) == "exact"
    assert names_match(token_set("Wei"), token_set("Wei")) is None
    assert names_agree("Evangelia Kalyvianaki", "Eva Kalyvianaki")
    assert not names_agree("Woosuk Chung", "Taeho Hwang")


def test_parse_orcid_accepts_typed_in_and_urls():
    assert hotcrp.parse_orcid("0000-0002-1825-0097?") == "0000-0002-1825-0097"
    assert hotcrp.parse_orcid("https://orcid.org/0000-0002-1825-009x") == "0000-0002-1825-009X"
    assert hotcrp.parse_orcid("https://dblp.org/pid/28/5155") is None


def test_tag_names_strip_twiddles_and_values():
    assert hotcrp.tag_names("~~fall0 fall0.5 track#3") == {"fall0", "fall0.5", "track"}


def test_an_export_with_no_pc_account_is_refused(tmp_path):
    path = write_export(tmp_path / "x.csv", [account("a@x.edu", roles="")])
    with pytest.raises(ValueError, match="no account has the 'pc' role"):
        hotcrp.load_export(path)


def test_an_address_in_both_exports_is_refused(tmp_path):
    pc = write_export(tmp_path / "pc.csv", [account("a@x.edu")])
    extra = write_export(tmp_path / "extra.csv", [account("A@x.edu")])
    with pytest.raises(ValueError, match="more than one user export"):
        hotcrp.load_exports([pc, extra])


def test_reviewers_from_an_export(tmp_path):
    path = write_export(tmp_path / "pc.csv", [
        account("b@x.edu", orcid="0000-0002-1825-0097?",
                **{"topic: Storage": "1", "topic: Networks": "2", "topic: Security": "-2"}),
        account("a@x.edu"),                                   # no ORCID: placed by hand
        account("c@x.edu"),                                   # no ORCID, not placed
        account("chair@x.edu", roles="chair pc"),
        account("ac@x.edu", tags="~~area-chairs"),
        account("off@x.edu", roles="pc", disabled="yes"),
    ])
    reviewers = roster.load_reviewers(path, roster.PC, placements={"a@x.edu": "28/5155"},
                                      cap_overrides={"b@x.edu": 2})
    by_email = {r.email: r for r in reviewers}
    assert list(by_email) == ["a@x.edu", "b@x.edu", "c@x.edu"]
    b = by_email["b@x.edu"]
    assert (b.pid, b.primary, b.secondary, b.override_cap) == (
        "orcid:0000-0002-1825-0097", "Networks", "Storage", 2)
    assert b.disliked_topics == (("Security", -2),)
    assert by_email["a@x.edu"].pid == "orcid:dblp:28/5155"
    assert by_email["c@x.edu"].pid is None


def test_placements_skip_blank_rows_and_refuse_garbage(tmp_path):
    path = tmp_path / "p.csv"
    path.write_text("email,dblp,note\nA@x.edu,https://dblp.org/pid/28/5155.html,\nb@x.edu,,decided not to\n")
    assert roster.load_placements(str(path)) == {"a@x.edu": "28/5155"}
    path.write_text("email,dblp,note\na@x.edu,not a link,\n")
    with pytest.raises(ValueError):
        roster.load_placements(str(path))


def test_upload_clears_then_lists_best_match_first(tmp_path):
    r = {e: roster.Reviewer(e, e.upper(), "", "", None, "", "", "", "", "", "pc", None)
         for e in ("a@x", "b@x")}
    affinity = {("a@x", 1): 0.9, ("b@x", 1): 0.95}
    out = tmp_path / "u.csv"
    hotcrp.write_upload(str(out), {1: ["a@x", "b@x"]}, affinity, r, clear_emails=["a@x"])
    assert list(csv.reader(open(out))) == [
        ["paper", "action", "email", "round"], ["all", "clearreview", "A@X", "R1"],
        ["1", "primaryreview", "B@X", "R1"], ["1", "primaryreview", "A@X", "R1"]]
    assert hotcrp.load_upload_pairs(str(out)) == {1: {"a@x", "b@x"}}


def test_the_production_config_loads_and_typos_are_refused(tmp_path):
    cfg = config.load(str(config.ROOT / "config.yaml"))
    assert cfg["assignment"]["reviewers_per_paper"] == 4
    assert cfg["caps"]["extra_by_submissions"] == {2: 7, 3: 8}
    bad = tmp_path / "c.yaml"
    bad.write_text("name: x\ninputs: {pcinfo: a, extra_pcinfo: b, papers: c, authors: d}\n"
                   "assignment: {reviewers_per_papr: 3}\n")
    with pytest.raises(ValueError, match="reviewers_per_papr"):
        config.load(str(bad))
