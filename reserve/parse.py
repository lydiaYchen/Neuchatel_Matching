"""One paper's reserve reviewer field, turned into a person.

Authors type the field freely: "Name, Affiliation, email, webpage", a
markdown table, one field per line, a mailto link, or a sentence asking for an
exemption. The person is then matched to the paper's authors and contacts,
first by email and then by name, which fills in missing names and fixes the
email of an author who wrote a different address.
"""

from __future__ import annotations

import re

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
URL_RE = re.compile(r"https?://\S+")
TITLE_RE = re.compile(r"^(?:(?:dr|prof|professor)\.?\s+)+", re.IGNORECASE)
# Leftover labels such as "E-mail:" or "Google Scholar:" once the value is removed.
LABEL_RE = re.compile(r"^[\w\s-]+:$")
FILLER_PARTS = {"none", "phd", "()"}
MAX_LINE_WORDS = 12
MAILTO_RE = re.compile(r"\[([^\]]*)\]\(mailto:[^)]*\)")
SKIP_VALUES = {"exempt", "none", "noen", "n/a", "na"}
# Words that mark the entry as an exemption sentence rather than a person.
NOT_NAME_WORDS = {"pc", "author", "authors", "exception", "applicable", "paper", "phd"}

# The email status of a reviewer named by several papers is the most serious one.
EMAIL_STATUS_ORDER = ["unmatched", "modified", "ok"]


def people_of(paper: dict) -> list[dict]:
    """Authors and contacts of a paper, used to fill in missing reviewer fields."""
    return paper.get("authors", []) + paper.get("contacts", [])


def normalize_markdown(text: str) -> str:
    """Turn a markdown `| **Field** | Value |` table into 'name, affiliation, email, url'."""
    fields = dict(
        (key.strip("* ").lower(), value.strip())
        for key, value in re.findall(r"^\|([^|]+)\|([^|]+)\|$", text, re.MULTILINE)
    )
    return ", ".join(fields.get(key, "") for key in ("name", "affiliation", "email", "webpage"))


def skip_reason(raw: str) -> str:
    """How a field that names nobody is counted in the summary."""
    key = raw.strip().strip('"').strip().lower()
    if key.startswith("n/a"):
        return "n/a"
    return key if key in SKIP_VALUES else "exemption sentence"


def parse(text: str, paper: dict) -> dict | None:
    """{firstName, lastName, email, affiliation, website, emailStatus}, or None
    when the field names nobody."""
    text = text.strip().strip('"').strip()
    if not text or text.lower() in SKIP_VALUES or text.lower().startswith("n/a"):
        return None
    if text.startswith("#") or "|" in text:
        text = normalize_markdown(text)
    # Some entries put each field on its own line; join them, but drop notes and
    # long explanations after the first line. Unwrap markdown links like [a@b.c](mailto:a@b.c).
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    lines = lines[:1] + [
        line
        for line in lines[1:]
        if not line.lower().startswith("note") and len(line.split()) <= MAX_LINE_WORDS
    ]
    text = MAILTO_RE.sub(r"\1", ", ".join(lines))
    # Unwrap <https://...> and <name@example.com>, and drop curly quotes.
    text = re.sub(r"<([^<>]*)>", r"\1", text).replace("“", "").replace("”", "")

    email_match = EMAIL_RE.search(text)
    email = email_match.group(0) if email_match else ""
    url_match = URL_RE.search(text)
    website = url_match.group(0).rstrip(",;") if url_match else ""
    # Drop a stray closing parenthesis, e.g. "...user=XXXX)".
    if website.endswith(")") and "(" not in website:
        website = website[:-1]

    rest = URL_RE.sub("", EMAIL_RE.sub("", text))
    parts = [
        p.strip()
        for p in re.split(r"[,;]", rest.replace("()", ""))
        if p.strip() and p.strip().lower() not in FILLER_PARTS and not LABEL_RE.match(p.strip())
    ]
    name = TITLE_RE.sub("", parts[0]) if parts else ""
    affiliation = ", ".join(parts[1:])
    words = re.findall(r"[a-z]+", name.lower())
    if not email and (len(words) > 4 or NOT_NAME_WORDS & set(words)):
        return None

    # Match against the paper's authors/contacts by email, then by name.
    match = None
    for person in people_of(paper):
        if email and person.get("email", "").lower() == email.lower():
            match = person
            break
    if match is None and name:
        for person in people_of(paper):
            full = f"{person.get('given_name', '')} {person.get('family_name', '')}"
            if full.strip().lower() == name.lower():
                match = person
                break

    if match is not None:
        first_name = match.get("given_name", "")
        last_name = match.get("family_name", "")
        affiliation = affiliation or match.get("affiliation", "")
    else:
        tokens = name.split()
        first_name = " ".join(tokens[:-1])
        last_name = tokens[-1] if tokens else ""

    # The reserve reviewer must be an author, so their email should be in the
    # paper's author list. If only the name matches an author, use that
    # author's email instead of the one written (or missing).
    authors = paper.get("authors", [])
    author_emails = {a.get("email", "").strip().lower() for a in authors}
    same_name = [
        a
        for a in authors
        if f"{a.get('given_name', '')} {a.get('family_name', '')}".strip().lower()
        in {name.lower(), f"{first_name} {last_name}".strip().lower()}
    ]
    if email.lower() in author_emails:
        email_status = "ok"
    elif same_name:
        email = same_name[0].get("email", "").strip()
        email_status = "modified"
    else:
        email_status = "unmatched"

    return {
        "firstName": first_name,
        "lastName": last_name,
        "email": email,
        "affiliation": affiliation,
        "website": website,
        "emailStatus": email_status,
    }
