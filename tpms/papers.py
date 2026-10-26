"""The submissions zip, renamed for TPMS.

HotCRP names each PDF `eurosys27-fall-paper<id>.pdf`; TPMS wants
`paper<id>.pdf`. The PDFs are copied into a new zip unchanged, with the same
compression as in HotCRP's zip.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
import zipfile
from collections import Counter
from pathlib import Path

_PAPER_RE = re.compile(r"paper(\d+)\.pdf$", re.IGNORECASE)


def tpms_name(name: str) -> str:
    """`paper<id>.pdf` for one entry of HotCRP's zip."""
    match = _PAPER_RE.search(name.rsplit("/", 1)[-1])
    if not match:
        raise ValueError(f"{name}: not a paper<id>.pdf")
    return f"paper{int(match[1])}.pdf"


def rename(src: str, dst: Path) -> int:
    """Write `dst` with every PDF of `src` renamed; returns the number of papers."""
    if not os.path.exists(src):
        raise FileNotFoundError(f"{src}: submissions zip not found")
    with zipfile.ZipFile(src) as zin:
        entries = [e for e in zin.infolist() if not e.is_dir()]
        names = [tpms_name(e.filename) for e in entries]
        duplicates = sorted(n for n, count in Counter(names).items() if count > 1)
        if duplicates:
            raise ValueError(f"{src}: more than one PDF for {', '.join(duplicates)}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=dst.parent, prefix=f".{dst.name}.",
                                             suffix=".tmp", delete=False) as f:
                temporary = Path(f.name)
                with zipfile.ZipFile(f, "w") as zout:
                    for entry, name in zip(entries, names):
                        info = zipfile.ZipInfo(name, entry.date_time)
                        info.compress_type = entry.compress_type
                        with zin.open(entry) as pdf, zout.open(info, "w") as out:
                            shutil.copyfileobj(pdf, out, 1 << 20)
            os.replace(temporary, dst)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    print(f"[papers] {len(names)} PDF(s) renamed to paper<id>.pdf", file=sys.stderr)
    return len(names)
