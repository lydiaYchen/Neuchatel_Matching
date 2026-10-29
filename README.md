# Nechatel Matching Systems

Based on the HPCA'27 matching system, the Neuchatel matching system was developed for EuroSys 2027 Fall to handle a doubling of paper submissions. Starting from raw HotCRP downloads, it generates formatted files ready for direct upload back into HotCRP. While its current pipeline and implementation details are tailored specifically for EuroSys 2027 rather than structured as a general-purpose tool, it can easily be adapted for other conferences.

The matching in short: every reviewer and every paper gets a SPECTER2
"fingerprint" (a vector summarising their research with similar research giving similar vectors). Reviewers are identified by DBLP page, found by the ORCID in their HotCRP profile. Pairs with a conflict of interest are removed. The remaining pairs are matched by similarity, within per-reviewer loads and per-paper limits.

## Contents

1. [Setup (once)](#setup-once)
2. [Running an assignment](#running-an-assignment)
3. [Manual corrections](#manual-corrections)
4. [Reproducing a run](#reproducing-a-run)
5. [Desk rejects](#desk-rejects)
6. [Reserve reviewers](#reserve-reviewers)
7. [Conflicts in HotCRP](#conflicts-in-hotcrp)
8. [Files for TPMS](#files-for-tpms)
9. [Code layout and tests](#code-layout-and-tests)

How the matching works, step by step: [ALGORITHM.md](ALGORITHM.md).

## Setup (once)

1. Install [uv](https://docs.astral.sh/uv/) and run in this directory:

   ```bash
   uv sync
   ```

2. Get a free [OpenAlex](https://openalex.org) API key and put it in a file
   `.env` in this directory (never commit it):

   ```
   OPENALEX_API_KEY=your-key
   ```

   Without a key OpenAlex allows about 1,000 requests a day, which a first
   run exceeds (though if you are willing to run it over several days, you could skip the API key).

3. Nothing else. The SPECTER2 model (about 800 MB) is downloaded from
   Hugging Face the first time something is embedded. A CUDA GPU is used when there is one. On a laptop CPU embedding runs at about 12 documents a
   second, so a first full run (about 17,000 documents) takes about 25 minutes. Later runs embed only reviewers and papers that changed via a cache.

## Running an assignment

### 1. Download from HotCRP, as a chair

Log in as a program chair and put these four files in `inputs/`:

| File | Where in HotCRP |
|---|---|
| PC members' user export | **Users** → list **Program committee** → select the PC members → **Download** → **PC info** → Go |
| Extra reviewers' user export | the same, selecting the extra reviewers. Keep it a separate file: being in this file is what makes someone an extra reviewer. An address in both files is an error |
| Paper JSON | **Search** → scope **Submitted**, empty query → select all → **Download** → **JSON** (not "JSON with attachments") → Go. As a chair, or the declared conflicts (`pc_conflicts`) are missing |
| Authors CSV | **Search** → scope **Submitted** → select all → **Download** → **Authors** → Go (every author's ORCID and country) |

Ask every reviewer, PC or extra, to fill in their HotCRP profile before you
download: **topic interests, ORCID, country and collaborators**. A reviewer
without an ORCID is matched on their topics only which results in significantly lower quality matching (for that specific reviewer). Reviewers without both topics of interests and ORCIDs are not eligible for matching.

### 2. Edit `config.yaml`

`config.yaml` holds the fall 2027 settings. Copy it (say to
`config-spring.yaml`), give it a new `name`, and point `inputs` at the new
files. The keys:

| Key | Fall 2027 | Meaning |
|---|---|---|
| `name` | `eurosys27-fall` | the run directory is `runs/<name>/` |
| `inputs.*` | | the four downloads |
| `curated.*` | | hand corrections, see [below](#manual-corrections). A missing file is fine |
| `caches.*` | `cache/…` | DBLP and OpenAlex results, shared between runs so nothing is fetched twice |
| `exclude_tags` | `[fall0]` | accounts carrying one of these HotCRP tags get no papers |
| `exclude_papers` | `[]` | paper IDs to leave out, such as a test submission. Only papers with status `submitted` are assigned |
| `fingerprints.years` | 4 | a reviewer's DBLP papers of the last 4 calendar years |
| `fingerprints.fallback_papers`, `fallback_years` | 10, 6 | a reviewer with no paper in that window gets their 10 most recent papers of the last 6 years instead |
| `caps.pc`, `caps.extra` | 4, 5 | papers per PC member / per extra reviewer |
| `caps.extra_by_submissions` | `{2: 7, 3: 8}` | an extra reviewer who authored at least 2 (3) of the submissions takes 7 (8) papers |
| `assignment.reviewers_per_paper` | 4 | |
| `assignment.max_extra_per_paper`, `max_pc_per_paper` | 3, 3 | so every paper has 1 to 3 PC members |
| `assignment.same_country_cap` | 1 | at most this many reviewers from the country most of a paper's authors are in |
| `assignment.coauthor_years` | 5 | a co-authored paper this recent is a conflict |
| `assignment.topic_veto` | -2 | a reviewer is never given a paper with a topic they rated -2 (`-1`: any negative rating) |
| `assignment.weak_pair_below` | 0.92 | afterwards, swap reviewers off pairs with similarity below this where that loses nothing |

### 3. Run

```bash
uv run python -m matching config.yaml
```

This fetches each reviewer's papers from DBLP and their abstracts from
OpenAlex (network), embeds them, assigns, and writes everything to
`runs/<name>/`. To rerun with cacche without any network or embedding:

```bash
uv run python -m matching config.yaml --skip-fingerprints
```

### 4. Check

The run ends with a line like this, also at the end of `runs/<name>/assign.log`:

```
Done. 3156 reviewer-paper pairs on 789 papers, 634 reviewers used. Must all be 0: 0 reviewers
over their cap; 0 conflicted pairs; 0 papers over the extra cap; 0 over the PC cap; 0 over the
same-country cap; 0 blocking pairs; 0 slots unfilled. ...
```

Every number after "Must all be 0" must be 0. With a slot unfilled no upload is written: loosen a cap or recruit reviewers. Then read `assignment.txt`:
every paper's reviewers best match first, the worst-matched papers, the
PC/extra mix, the same-country cap per country, what each conflict layer
excluded, and every reviewer's papers. To see why someone got (or did not
get) a paper:

```bash
uv run python -m matching.records runs/eurosys27-fall --email someone@uni.edu
```

This prints their DBLP page and how it was found, the publications their
fingerprint was built from, every conflict with its evidence, and their
papers.

### 5. Upload to HotCRP

Assignments → Upload, round R1. Use either:

* `pc_assignment.csv` and `extra_assignment.csv`. Each clears only its own
  pool's R1 reviews, so the two can be uploaded separately and in either
  order. Use these.
* `assignment.csv`, the same pairs in one file, which first clears **every**
  R1 review in HotCRP, including any placed by hand.

HotCRP shows a preview before it applies anything; check it there.

## Manual corrections

Optional CSV files in `curated/`, with the paths set in `config.yaml`. They
name real people, so they are never committed. In every file a row with a
blank value is skipped, so it can record a decision without acting on it.

**`dblp_placements.csv`** (`email,dblp,note`): this reviewer's DBLP page,
e.g. `https://dblp.org/pid/28/5155.html`. Use it when the record says
`placed_by` = `not found` (DBLP does not know their ORCID) or `no ORCID`, or
when the page found is someone else's. Only name a page after reading it,
and never a page that mixes two people.

**`fingerprint_overrides.csv`** (`email,source,years,note`):
- `dblp` with `years`, e.g. `8`: read their DBLP page over a wider window.
  For someone who published little recently.
- `area-only`: ignore DBLP and use their HotCRP topics only. For a page that
  is not theirs.
- `own-papers`: use the abstracts of their own submissions instead of DBLP.

**`cap_overrides.csv`** (`email,cap,note`): a different paper load for one
reviewer. It wins over `caps` and `extra_by_submissions`.

**`swaps.csv`** (`paper,out,in,note`): replace reviewer `out` by `in` on a
paper, after everything else. A swap between two papers is two rows. Each
swap is checked against every conflict and cap; the run fails rather than
apply one that breaks a rule.

## Reproducing a run

A run directory holds every piece of data the run used. With the same
inputs, `curated/` files and code, a `--skip-fingerprints` rerun reproduces
it exactly, offline.

This code was taken from the HPCA 2027 matcher the fall 2027 assignment was
made with, keeping only what EuroSys uses. Rerun on that assignment's
fingerprints and data, it reproduces it byte for byte: the three uploads,
`conflicts.csv` and `publications.csv`. `reviewers.csv` matches too, less a
redundant `tier` column. Rebuilding a sample of the fingerprints gives the
same vectors to within 6×10⁻⁸.

## Desk rejects

Before anything else, the `deskreject` package flags the papers a chair
should look at for a desk reject. It only flags; you decide, and you reject
the papers in HotCRP yourself.

### 1. Download from HotCRP, as a chair

Right after the deadline, put these in `inputs/`, next to the PC user export
and authors CSV the matching uses:

| File | Where in HotCRP |
|---|---|
| Paper JSON at the deadline | the same download as the matching's paper JSON, but now, before any desk reject. Keep it under its own name: the reserve reviewers read it too |
| Format check CSV | **Search** → scope **Submitted** → select all → **Download** → **Format check** → Go |

Then list the reserve reviewer exemptions the chairs approved in
`curated/reserve_exemptions.csv`, one paper ID per row (a missing file means
none):

```
paper
12
345
```

### 2. Check

```bash
uv run python -m deskreject config.yaml
```

A paper is flagged when:

* an author is on more than `max_submissions` (3) submissions. Authors are
  told apart by ORCID, then email, then name;
* it has no reserve reviewer with an email address, and no valid exemption:
  an author on the PC, or the paper in `reserve_exemptions.csv`;
* the format check found too many pages, too small a body font, or margins
  too small by more than `margin_over` (5) percent, and the paper JSON still
  reports the problem. Almost every paper overflows the margins by up to 4%,
  so smaller margin problems are ignored.

The list is `runs/<name>/deskreject/flagged.csv` (`paper,reason`).

### 3. Decide, and reject in HotCRP

Copy the list to `curated/desk_rejects.csv` and add a column `Decision` with
`Y` (desk reject) or `N` on every row. You can add a row for a paper no rule
flagged:

```
Decision,paper,reason
Y,17,Format: Too many pages: 14 pages
N,52,Reserve reviewer: incomplete entry 'Jane Doe' (no email)
Y,88,decided by hand
```

Then desk-reject the `Y` papers in HotCRP and download the paper JSON again
for the matching and TPMS (`inputs.papers`): it now holds only the papers
still submitted. The reserve reviewers read `desk_rejects.csv` too.

| Key | Fall 2027 | Meaning |
|---|---|---|
| `deskreject.papers` | `inputs/eurosys27-fall-data-before-desk-reject.json` | the paper JSON at the deadline |
| `deskreject.formatcheck` | `inputs/eurosys27-fall-formatcheck.csv` | HotCRP's format check |
| `deskreject.exemptions` | `curated/reserve_exemptions.csv` | approved reserve reviewer exemptions. A missing file is fine |
| `deskreject.decisions` | `curated/desk_rejects.csv` | `flagged.csv` with `Decision` filled in |
| `deskreject.max_submissions` | 3 | submissions allowed per author |
| `deskreject.margin_over` | 5 | margin problems up to this percentage are ignored |

The texts of the "Reserve reviewer type" options on the submission form are
in `deskreject/check.py`. Change them there if the form changes.

## Reserve reviewers

Every submission names a reserve reviewer, one of its authors who agrees to
review if asked. The `reserve` package turns those names into the list of
extra reviewers. It runs in two steps with a decision by hand in between, so
you run the same command twice.

### 1. Decide the desk rejects first

Start once `curated/desk_rejects.csv` is filled in (see
[Desk rejects](#desk-rejects)). The reserve reviewers are read from the paper
JSON at the deadline (`deskreject.papers`), so the desk-rejected papers are
still there. That is on purpose: when a paper is desk-rejected, nobody it
named is needed any more, even if another paper named them too. A PC member
it named comes off the PC.

### 2. Extract

```bash
uv run python -m reserve config.yaml
```

This reads each paper's "Reserve reviewer" field, which authors type
freely: a name with an affiliation and email, a markdown table, or a
sentence asking for an exemption. Each name is matched to the paper's authors,
a person named by several papers is kept once, and each person gets some
hints for deciding: on the PC or not, academia or industry (from ROR), PhD or
not, and publication counts (from ORCID and OpenAlex). This needs the
network the first time; the answers are cached in `cache/reserve/`.

Anyone a desk-rejected paper named is left out. If one of them is on the PC,
the run warns and lists them in `runs/<name>/reserve/pc_to_remove.csv`:
remove them from the PC in HotCRP and download the PC export again. Once
they are gone from the export, the list comes out empty.

The list is `runs/<name>/reserve/reserve_reviewers.csv`, one row per person.
Reviewers whose `emailStatus` is `unmatched` come first: they match no author
of their paper, so check them by hand. `modified` means the email typed did
not match, so the author's email on the paper was used instead. The run also
lists every paper whose field names nobody.

### 3. Decide

Copy the list to `curated/reserve_decisions.csv` and add a column `Decision`
with `Y` (invite) or `N` (don't) on every row:

```
Decision,paper,firstName,lastName,email,affiliation,website,emailStatus,onPCList,sector,hasPhD,orcidPublications,openalexPublications
Y,12; 40,Jane,Doe,jane.doe@university.example.edu,Example University,,ok,no,academia,yes,35,41
N,7,Wei,Chen,wei.chen@lab.example.org,Example Lab,,ok,no,industry,unknown,3,5
```

A blank Decision counts as N, and the run warns about each one. You may fix
a name, email or affiliation in this file too: the final list takes them from
here. People on the PC are dropped whatever their Decision.

### 4. Finalise

Run the same command again:

```bash
uv run python -m reserve config.yaml
```

Now that the decisions file exists, it also writes
`runs/<name>/reserve/final_reserve_reviewers.csv`: everyone with `Y`, not on
the PC, and with at least one paper that named them still submitted (a paper
may be withdrawn after you decide). Each
row has the ORCID found for their email in the authors CSV (blank if none),
how many submissions they authored and how many papers named them.
Affiliations stay as the authors typed them.

### 5. Add them to HotCRP

This step is by hand: create the final reserve reviewers as accounts in
HotCRP. Afterwards download their user export (`inputs.extra_pcinfo`, see
[above](#1-download-from-hotcrp-as-a-chair)); the matching reads it.

| Key | Fall 2027 | Meaning |
|---|---|---|
| `reserve.field` | `Reserve reviewer` | the submission field naming the reserve reviewer |
| `reserve.decisions` | `curated/reserve_decisions.csv` | the list with `Decision` filled in. Until it exists, only step 2 runs |
| `reserve.cache` | `cache/reserve` | the ORCID, ROR and OpenAlex answers, one file each |

## Conflicts in HotCRP

The `conflicts` package makes conflict uploads for HotCRP (Assignments →
Upload), in two ways. Run it once the extra reviewers are in HotCRP, and
download the conflicts CSVs again afterwards: TPMS and the matching read them.

### 1. Download from HotCRP, as a chair

| File | Where in HotCRP |
|---|---|
| Conflict page of each site | **Assignments** → **Conflicts**, then save the page in the browser (**Save Page As** → *Web Page, HTML only*). Once on the PC site and once on the extra reviewers' site |
| Extra reviewers' user export and conflicts | the user export as for the matching (`inputs.extra_pcinfo`), and **PC conflicts** from the extra reviewers' site |

### 2. Run

```bash
uv run python -m conflicts config.yaml
```

It writes to `runs/<name>/conflicts/`. A step runs only when its file is set
in `conflicts:`:

* `pc_page_conflicts.csv` and `extra_page_conflicts.csv`: every potential
  conflict on the site's conflict page, confirmed. The page lists what HotCRP
  found itself (matching names, affiliations, collaborators), so read it
  before uploading if you want to confirm only some. The page shows names, not
  emails, so each reviewer is looked up by name in the same site's user export.
  When two accounts share a name, HotCRP shows the email too, and that is used.
  A reviewer not found is reported and left out.
* `extra_affiliation_conflicts.csv`: every extra reviewer who works where an
  author of a paper does, and HotCRP doesn't have that conflict yet. The extra
  reviewers were added after submission, so authors could not declare
  conflicts with them. "Works where" means the same affiliation or the same
  email domain (webmail matches nothing). `extra_affiliation_evidence.csv`
  lists every conflict found, with which signal found it and whether HotCRP
  has it already.

Every upload has the columns `paper,action,email,conflict`. HotCRP shows a
preview before applying it. The pages change after an upload, so download a
page again before running again.

### 3. Affiliations that need help

A reviewer's affiliation is searched for in the authors' affiliations as whole
words, along with common abbreviations (`cmu`, `hkust`, ...). For an
affiliation that needs something else, add a row to
`curated/affiliation_terms.csv` (`affiliation,terms`, terms separated by
`;`). Use it for a combined affiliation, an abbreviation, or a company whose
papers name a subsidiary. The `affiliation` must match the user export
exactly:

```
affiliation,terms
NVIDIA & Stanford University,nvidia; stanford university; stanford
Google,google; deepmind
Example Tech Co. Ltd.,example tech
```

| Key | Fall 2027 | Meaning |
|---|---|---|
| `conflicts.pc_page` | `inputs/eurosys27-fall-conflicts.html` | the PC site's conflict page |
| `conflicts.extra_page` | `inputs/eurosys27-extra-conflicts.html` | the extra reviewers' site's conflict page |
| `conflicts.extra_pcconflicts` | `inputs/eurosys27-extra-pcconflicts.csv` | the conflicts HotCRP has for the extra reviewers |
| `conflicts.affiliation_terms` | `curated/affiliation_terms.csv` | search terms for some affiliations. A missing file is fine |

## Files for TPMS

The `tpms` package makes the files TPMS takes, from the same `config.yaml`:
the papers, and the conflicts and reviewer info of the PC and of the extra
reviewers. It has nothing to do with the matching above and needs no
network or GPU.

### 1. Download from HotCRP, as a chair

Put these in `inputs/`, next to the PC members' user export the matching
already uses (`inputs.pcinfo`):

| File | Where in HotCRP |
|---|---|
| Submissions zip | **Search** → scope **Submitted** → select all → **Download** → **Submissions** (under Documents) → Go. Each PDF is named `eurosys27-fall-paper<id>.pdf` |
| PC conflicts | **Search** → scope **Submitted** → select all → **Download** → **PC conflicts** → Go |
| Extra reviewers' conflicts | the same download from the HotCRP site the extra reviewers are on, once they have been added (see [Reserve reviewers](#reserve-reviewers)) |

### 2. Write the two email lists

Both go in `curated/` and are never committed. Either can be missing, and a
missing file simply means nobody is affected.

**`chairs.csv`** (`email`): the chairs. Their conflicts are left out, and they
get no papers.

```
email
chair.one@example.edu
chair.two@example.org
```

**`email_changes.csv`** (`old_email,new_email`): a PC member whose TPMS account
is under another address than their HotCRP one. Their HotCRP address is
replaced in both CSV files. A row with a blank value is skipped.

```
old_email,new_email
jane.doe@university.example.edu,jdoe@gmail.example.com
wei.chen@lab.example.org,wei.chen@university.example.edu
```

### 3. Set the paper loads in `config.yaml`

Everything for TPMS sits under `tpms:`. The fall 2027 settings:

```yaml
tpms:
  papers: inputs/eurosys27-fall-papers.zip
  pcconflicts: inputs/eurosys27-fall-pcconflicts.csv
  chairs: curated/chairs.csv
  email_changes: curated/email_changes.csv
  limits:
    - {tag: fall0, min: 0, max: 0}
  default_limits: {min: 4, max: 5}
  extra_pcconflicts: inputs/eurosys27-extra-pcconflicts.csv
  extra_limits: {min: 5, max: 6}
```

| Key | Fall 2027 | Meaning |
|---|---|---|
| `tpms.papers`, `tpms.pcconflicts` | | the two downloads |
| `tpms.chairs`, `tpms.email_changes` | `curated/…` | the two email lists. A missing file is fine |
| `tpms.limits` | `fall0` → 0 to 0 | paper loads by HotCRP tag, see below |
| `tpms.default_limits` | 4 to 5 | the load of a PC member no rule names |
| `tpms.extra_pcconflicts` | | the extra reviewers' conflicts. Leave it out for no extra reviewer files |
| `tpms.extra_limits` | 5 to 6 | the load of every extra reviewer |

A PC member's load (`min` and `max` papers) is decided like this:

1. a chair gets 0 to 0;
2. otherwise the first rule in `limits` whose tag they carry decides it;
3. a PC member no rule names gets `default_limits`.

Order the rules from most to least specific. Tags are compared whole, so
`spring0` does not match `spring0.5`. A rule can also leave someone out of
`reviewer_info.csv` altogether, for instance PC members with no PDF on TPMS.
Rules in the style of spring 2027 look like this:

```yaml
  limits:
    - {tag: tpmsnopdf, leave_out: true}   # no row at all
    - {tag: spring0.5, min: 3, max: 4}    # half load
    - {tag: spring0, min: 0, max: 0}
    - {tag: fall0, min: 0, max: 0}
  default_limits: {min: 6, max: 8}
```

The run prints every tag it finds in the export with a count, which helps
when choosing the rules, and warns about a rule whose tag nobody carries.

### 4. Run and upload

```bash
uv run python -m tpms config.yaml
```

This writes to `runs/<name>/tpms/`:

* `papers.zip`: the submissions, each renamed `paper<id>.pdf`. The PDFs
  themselves are unchanged.
* `pcconflicts.csv` (`paperID,reviewerEmail`): every PC conflict, minus the
  chairs.
* `reviewer_info.csv` (`Email,FirstName,LastName,min,max`): one row per PC
  member, in the order of the export.
* `extra_pcconflicts.csv` and `extra_reviewer_info.csv`: the same for the
  extra reviewers. They are the reserve step's
  `runs/<name>/reserve/final_reserve_reviewers.csv`, so run that first.

Check the warnings before uploading. They tell you when an address in either
email list is not in the user export, which usually means a typo. They also
tell you when a conflict names an address that isn't in the reviewer info,
because TPMS can't apply that conflict. This usually means an extra reviewer
whose HotCRP account has a different address from the one on the final reserve
list. Add a row for them to `email_changes.csv` (HotCRP address first) so both
files use one address. In fall 2027 that was 15 extra reviewers.

## Code layout and tests

```
matching/
  pipeline.py      the four stages: roster, fingerprints, assignment, records
  config.py        config.yaml and the files of a run directory
  hotcrp.py        reading HotCRP's downloads, writing its uploads
  roster.py        the reviewer pools
  names.py         comparing names across HotCRP and DBLP
  dblp.py          papers and co-authors from DBLP by ORCID (SPARQL)
  openalex.py      abstracts by DOI
  embedding.py     SPECTER2
  fingerprints.py  reviewer and paper fingerprints
  authors.py       paper authors placed on DBLP, for the co-author layer
  coi/             the conflict layers, one module each
  assign/          deferred acceptance, repair, countries, report
  records.py       reviewers.csv, publications.csv, --email
reserve/
  pipeline.py      the two steps: extract, finalise
  config.py        the reserve: section of config.yaml and the output directory
  parse.py         one paper's reserve reviewer field, turned into a person
  lookups.py       the hints: sector (ROR), PhD and publications (ORCID, OpenAlex)
  extract.py       reserve_reviewers.csv
  finalise.py      final_reserve_reviewers.csv
deskreject/
  pipeline.py      the check, then the decisions
  config.py        the deskreject: section of config.yaml and the output directory
  check.py         the three rules, flagged.csv
  people.py        authors' ORCIDs and the PC, shared with reserve/
conflicts/
  pipeline.py      the two steps: pages, affiliations
  config.py        the conflicts: section of config.yaml and the output directory
  pages.py         HotCRP's conflict page, read into an upload
  institutions.py  same affiliation, same email domain
  affiliations.py  the extra reviewers' institution conflicts
tpms/
  pipeline.py      the three steps: papers, conflicts, reviewers
  config.py        the tpms: section of config.yaml and the output directory
  papers.py        the renamed submissions zip
  emails.py        the chairs and email changes lists
  conflicts.py     pcconflicts.csv
  reviewers.py     reviewer_info.csv
tests/
```

**Never commit data.** `inputs/`, `curated/`, `cache/`, `runs/` and `.env`
are in `.gitignore`: they hold names, emails, conflicts and submissions.
