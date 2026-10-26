"""Which papers a fingerprint is built from, and the content keys of the caches."""

from matching import dblp, fingerprints
from matching.roster import Reviewer

from .helpers import author, paper


def test_content_keys_never_change_silently():
    # Computed with the code the 2027 fall production run used. A different
    # value means every cached fingerprint would be rebuilt.
    r = Reviewer(email="ada@example.org", hotcrp_email="ada@example.org", first="Ada",
                 last="Lovelace", pid="orcid:0000-0002-1825-0097", affiliation="Example",
                 primary="Operating systems", secondary="Storage", tertiary="",
                 keywords="Networks", pool="pc", override_cap=None)
    pubs = [(2025, "A paper", "10.1/x", "An abstract.", "openalex"), (2024, "Another", "", "", "")]
    assert fingerprints.fingerprint_key(r, pubs, 4, "dblp") == \
        "aa1e415fe2fd17f4a35e2effc2335226de53b018d8cc71a3e44d195ce843643a"
    assert fingerprints.fingerprint_key(r, [], 4, "area-only") == \
        "8324e0c4b5c2ee661c805dfbee29a0443b70a4e46e0c881f00b33e8ceac7aa60"
    assert fingerprints._paper_key({"title": "T", "abstract": "A.", "topics": ["Storage", "Networks"]}) \
        == "9cf14faed031efc7fd49aff614dfeccaa860c30d"


def record(year, title, *, arxiv=False, doi=""):
    return {"year": year, "title": title, "arxiv": arxiv, "doi": doi,
            "coauthors": [], "coauthor_pids": {}}


def test_publications_dedupe_copies_and_drop_arxiv_only_papers():
    records = [record(2025, "Fast Storage.", doi="10.1/pub"),
               record(2024, "Fast storage", arxiv=True, doi="10.48550/arxiv.1"),
               record(2025, "Only a preprint", arxiv=True),
               record(2019, "Too old")]
    assert dblp.to_publications(records, 2023, exclude_arxiv=True) == [
        (2025, "Fast Storage.", ["10.1/pub", "10.48550/arxiv.1"])]
    assert [t for _, t, _ in dblp.fallback_publications(records, 2019, 3)] == [
        "Fast Storage.", "Only a preprint", "Too old"]


def test_basis_says_what_a_fingerprint_rests_on():
    def basis(entry, pubs=(), **kw):
        kw = {"override": None, "fell_back": False, "from_submissions": False, **kw}
        return fingerprints.basis_of(None, entry, "k", list(pubs), **kw)
    cached = {"fingerprint_key": "k"}
    assert basis(None) == "none"
    assert basis({"fingerprint_key": "older"}) == "stale"
    assert basis(cached, [1]) == "dblp"
    assert basis(cached, [1], fell_back=True) == "dblp-fallback"
    assert basis(cached) == "topics"
    assert basis(cached, from_submissions=True) == "submission-topics"
    assert basis(cached, override="area-only") == "topics-override"
    assert basis(cached, [1], override="own-papers") == "own-submissions"


def test_reviewers_with_nothing_take_their_submissions_topics():
    r = Reviewer("a@x.edu", "a@x.edu", "A", "B", None, "", "", "", "", "", "extra", None)
    papers = [paper(1, topics=["Storage", "Networks"], authors=[author("a@x.edu")]),
              paper(2, topics=["Storage"], authors=[author("a@x.edu")])]
    filled, topics = fingerprints.submission_topics([r], {"a@x.edu"}, papers)
    assert (filled[0].primary, filled[0].secondary) == ("Storage", "Networks")
    assert topics == {"a@x.edu": ["Storage", "Networks"]}


def test_overrides_file_is_validated(tmp_path):
    path = tmp_path / "o.csv"
    path.write_text("email,source,years,note\nA@x.edu,dblp,8,\nb@x.edu,area-only,,\n")
    assert fingerprints.load_overrides(str(path)) == {"a@x.edu": ("dblp", 8),
                                                      "b@x.edu": ("area-only", None)}
    path.write_text("email,source,years,note\na@x.edu,area-only,8,\n")
    try:
        fingerprints.load_overrides(str(path))
    except ValueError:
        return
    raise AssertionError("a years value without source dblp was accepted")
