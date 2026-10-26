# How the assignment works

A run has four stages (`matching/pipeline.py`):

1. **Roster**: who can review.
2. **Fingerprints**: what each reviewer and each paper is about.
3. **Assignment**: conflicts are removed, then reviewers are matched to papers.
4. **Records**: the per-reviewer record of what the run used.

Settings named in `code font` are keys of `config.yaml`. Figures marked
*fall 2027* come from the production run: 789 papers, 199 PC members and
463 extra reviewers.

## 1. Who reviews

Every account with the `pc` role in one of the two HotCRP user exports is a
reviewer, except:
- chairs, sysadmins, TRC members and accounts tagged `~~area-chairs`;
- accounts carrying a tag in `exclude_tags` (fall 2027: `fall0`, reviewers
  sitting this cycle out).

**The file decides the pool:** everyone in the PC export is a PC member and
everyone in the extra-reviewer export is an extra reviewer. No HotCRP tag is
read for this, and an address in both files is an error.

From each account the roster takes:
- **ORCID:** the reviewer's identity on DBLP. HotCRP's trailing `?` (typed
  in rather than authenticated) is accepted.
- **Topics rated +1 or +2:** ranked by score, ties alphabetically. They
  describe the reviewer in their fingerprint.
- **Topics rated −1 or −2:** read by the topic veto.
- **Affiliation, country and collaborators:** read by the conflict layers
  and the same-country cap.

Only papers with HotCRP status `submitted` are assigned, minus any in
`exclude_papers`.

## 2. Fingerprints

A fingerprint is a SPECTER2 embedding: a 768-number vector produced by a
language model trained on scientific papers, so that papers on similar
research get similar vectors. Each document is embedded in the model's own
format, `title [SEP] abstract`. A fingerprint is the weighted average of
several such documents, scaled to length 1. The **similarity** of a reviewer
and a paper is the dot product of their fingerprints, at most 1.

### Finding a reviewer on DBLP

DBLP, not OpenAlex or Google Scholar, decides which papers a reviewer wrote.
DBLP is curated by hand. OpenAlex profiles both split one person in two
(missing papers) and merge namesakes (adding other people's). Everything goes
through DBLP's SPARQL endpoint (`matching/dblp.py`).

An ORCID is placed on a DBLP person page by the first route that works:

| route (`placed_by`) | how | fall 2027 (PC / extra) |
|---|---|---|
| `person` | the DBLP person record lists the ORCID | 116 / 284 |
| `paper` | the ORCID sits on the author entry of their papers (from publisher metadata); the person credited with most of them is taken, if the name agrees with HotCRP's | 70 / 140 |
| `orcid-papers` | those papers belong to a DBLP *catch-all* page, which collects papers by anyone of that name that DBLP has not assigned. The page is never taken as the person; only the papers carrying the ORCID count, so their list is a lower bound | 5 / 27 |
| `hand` | `curated/dblp_placements.csv` names the page | 7 / 6 |
| `not found` | DBLP cannot place the ORCID (often one created just for HotCRP) | 1 / 6 |
| `no ORCID` | no ORCID in HotCRP and no placement | |

A name never decides who someone is. An ORCID DBLP cannot place is unknown
and is asked about again on the next run. It is never guessed by name.

### Which papers

From the DBLP page the fingerprint takes:
- **The window:** papers of the last `fingerprints.years` calendar years
  (4: 2023–2026). `fingerprint_overrides.csv` can widen it for one person.
- **One entry per paper:** copies with the same title, such as a preprint
  and the published version, count once and are dated by the newest copy.
- **No preprint-only papers:** a paper that exists only on arXiv is left
  out. An arXiv copy of a published paper still lends its DOI, which is often
  the only way an ICLR or USENIX paper finds an abstract.
- **A fallback:** someone with nothing in the window takes their
  `fallback_papers` (10) most recent papers of the last `fallback_years` (6),
  preprints included. These are mostly senior and industry reviewers.

Each paper's abstract is looked up on OpenAlex by DOI (`matching/openalex.py`).
A paper without one is embedded from its title. In fall 2027, 11,293 of the
14,600 embedded papers had an abstract.

### The topic document

One more document lists the reviewer's HotCRP topics, weighted like one
paper. For a reviewer with 20 papers that is 1/21 of the fingerprint. For a
reviewer without papers it is all of it.

### What a fingerprint rests on

`reviewers.csv` gives each reviewer's `basis`:

| basis | the fingerprint is built from | fall 2027 (PC / extra) |
|---|---|---|
| `dblp` | DBLP papers in the window, plus topics | 192 / 452 |
| `dblp-fallback` | the fallback papers, plus topics | 4 / 4 |
| `topics` | topics only: no ORCID, DBLP cannot place it, or no papers even in the fallback window | 2 / 3 |
| `submission-topics` | no papers and no topics: the topics of the submissions they authored | 0 / 3 |
| `own-submissions` | by hand: the abstracts of their own submissions | 0 / 1 |
| `topics-override` | by hand: topics only, DBLP ignored (a page that is not theirs) | 1 / 0 |
| `none` | nothing at all: no fingerprint, never assigned | 0 / 0 |
| `stale` | the cached fingerprint was not built from the current inputs (`--skip-fingerprints` after an input changed); rebuild | |

### Papers

A paper's fingerprint averages two documents with equal weight: title plus
abstract, and its HotCRP topics.

### Caching

Every fingerprint is cached under a key computed from everything that went
into it: the person, their papers and abstracts, their topics, the window and
the model. A reviewer or paper whose key has not changed is never embedded
again. After a fingerprint build the shared DBLP and OpenAlex caches are
frozen into the run directory, so a `--skip-fingerprints` rerun reproduces
the run offline.

## 3. Conflicts

Before any matching, every (reviewer, paper) pair is tested against six
layers (`matching/coi/`). One hit removes the pair for the whole run; no
later step can use it.

| layer | the pair is removed when | fall 2027 pairs |
|---|---|---|
| `declared` | authors or chairs marked a conflict in HotCRP (`pc_conflicts`), against either of the reviewer's addresses | 3,411 |
| `own-paper` | the reviewer is an author or contact of the paper | 803 |
| `coauthor` | the reviewer wrote a paper with one of its authors in the last `coauthor_years` (5) years | 8,641 |
| `collaborator` | the reviewer's HotCRP collaborators name an author, an author's collaborators name the reviewer, or the submission's own declared collaborators do | 1,771 |
| `institution` | the reviewer and an author share an organisation email domain or an affiliation | 10,328 |
| `topic-veto` | the reviewer rated one of the paper's topics `topic_veto` (−2) or lower | 148,934 |

The first five are conflicts of interest: 18,377 distinct pairs in fall
2027. The topic veto is the reviewer's own "not this topic", applied the
same way.

**Why the derived layers matter.** Authors can declare conflicts only
against accounts that are on the PC when they submit. The extra reviewers
were added afterwards, so few conflicts were declared against them; for them
the co-author, collaborator and institution layers are most of the
protection.

**Co-authors.** The reviewer's co-authors come from the same DBLP papers as
their fingerprint. Each paper author is placed on DBLP by the ORCID in the
authors CSV, the same way as a reviewer (`matching/authors.py`; fall 2027:
2,137 of 3,784 authors). Then:
- **Both sides on DBLP:** the pages are compared, so a reviewer who wrote
  with a namesake is not conflicted.
- **Otherwise:** names are compared, erring towards a conflict. A name equal
  to a co-author's, or the same name with a middle name added or dropped,
  matches. A lost reviewer slot costs less than a missed conflict.
- **Catch-all pages:** a co-author on one is always compared by name.

Fall 2027 matches: 5,247 by DBLP page, 2,145 by exact name, 119 by partial
name, 1,130 through a catch-all page. A reviewer DBLP cannot place has no
co-authors to compare and passes this layer (7 in fall 2027).

**Collaborators.** HotCRP's `collaborators` field holds one person per line.
It is checked in three directions: the reviewer's field against the
authors, each author's field (if they have an account) against the
reviewer, and the submission's own declared collaborators against the
reviewer.

**Institution.** Two signals, either one is enough:
- **Email domain:** the same organisation domain. `cs.x.edu.cn` and
  `mails.x.edu.cn` are one, `ed.ac.uk` keeps `ac.uk`, and webmail and alumni
  addresses never match.
- **Affiliation:** the same name, word for word and in order, after dropping
  articles and company suffixes ("Huawei Technologies Co., Ltd." equals
  "Huawei"). "University of Washington" differs from "Washington
  University", and HKU from HKUST.

An "All (Institution)" collaborator line, on the reviewer's profile or on
the submission, counts as that institution.

`conflicts.csv` lists every removed pair with its layer and evidence: the
co-authored papers, the matched name, the shared domain. Before it is
written, the run checks that it explains exactly the pairs the matcher
removed.

## 4. Assignment

Each paper ranks its eligible reviewers by similarity. The matcher then fills
4 slots per paper (`reviewers_per_paper`) under these limits, which hold in
every step below:

- **Loads:**
  - `caps.pc` (4) papers per PC member and `caps.extra` (5) per extra reviewer;
  - an extra reviewer who authored at least 2 (3) of the submissions takes 7 (8)
    papers (`caps.extra_by_submissions`, matched by email or ORCID; fall 2027:
    101 at 7, 70 at 8);
  - `curated/cap_overrides.csv` wins over both.
- **Mix:** at most `max_extra_per_paper` (3) extra and `max_pc_per_paper` (3)
  PC reviewers per paper, so every paper has 1–3 PC members.
- **Same country:** a paper takes at most `same_country_cap` (1) reviewers
  from the country most of its authors are in.
  - *A person's country*, first source that answers: their own `country` in
    HotCRP (the user exports, or the authors CSV), a country or region named
    in their affiliation, their email's country code. Never `.com`, `.edu`
    and the like.
  - *Country* means where the institution is, never nationality, and Hong
    Kong, Macao, Taiwan and Singapore are not counted as China.
  - *A paper is capped* only when at least half of its authors have a known
    country and more than half of those share one.
  - *Fall 2027:* 776 of 789 papers judged, 430 capped on China and 115 on the
    US.

The steps (`matching/assign/`):

1. **PC anchor.** Every paper first takes its best eligible PC member. PC
   capacity is scarce (4 × 199 = 796 slots for 789 papers), so this seats
   one PC member per paper before any paper can take a second.
2. **Fill.** The remaining slots are filled by *deferred acceptance*, the
   algorithm of the US medical residency match. Each paper proposes to its
   reviewers best first. A reviewer holds the best offers received so far, up
   to their load, and drops the worst one when a better one arrives. The
   dropped paper proposes to its next choice. When a per-paper limit would be
   broken, the candidate waits rather than being rejected, and gets the slot
   if one opens. The result is *stable*: no reviewer and paper would both
   rather have each other than one of their current matches.
3. **Repair.** A stable matching need not fill every slot. Once everyone
   eligible for a paper is full of papers they rank higher, the paper stays
   short. Each empty slot is filled by the shortest chain of moves: a
   reviewer with spare capacity takes another paper's place, and that paper's
   reviewer moves to the short paper, and so on. Every paper on a chain ends
   as full as it started. Fall 2027: 127 slots, 153 moves.
4. **Weak pairs.** Each pair below `weak_pair_below` (0.92), weakest first,
   is swapped with a reviewer on another paper, or replaced by a reviewer
   with spare capacity. A move is taken only if it creates no new weak pair,
   breaks no limit and loses no total similarity. Fall 2027: 50 moves.
5. **Hand swaps.** The chairs' replacements in `curated/swaps.csv`, checked
   against every conflict and limit like the matcher's own pairs. Fall 2027:
   8.

A paper's **match goodness** is the mean similarity of its reviewers. Fall
2027: mean 0.961, worst 10 papers 0.935. Every assigned pair lies between
0.92 and 0.98. 782 papers have 1 PC member and 7 have 2.

**Self-checks**, printed at the end of the run, all of which must be 0:
- reviewers over their cap;
- assigned pairs that a conflict layer excludes;
- papers over the extra, PC or same-country cap;
- blocking pairs in the fill (checked on the papers without a country cap,
  where stability is guaranteed);
- unfilled slots. With any slot unfilled, no upload is written.

## 5. Reviewer records

The fingerprint stage writes what each fingerprint was built from:
- the DBLP page and how it was found;
- the exact papers embedded and which had an abstract;
- the basis.

`matching/records.py` joins this with `conflicts.csv` and the upload into:
- `reviewers.csv`: one row per reviewer, with the number of papers each layer
  kept them off;
- `publications.csv`: one row per embedded paper.

`--email` prints one reviewer's full record.
