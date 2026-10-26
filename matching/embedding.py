"""SPECTER2 embeddings and the JSON caches that hold them.

SPECTER2 (`allenai/specter2_base` with the `allenai/specter2` proximity
adapter) turns a paper's `title [SEP] abstract` into a 768-dimensional
vector; similar research gives similar vectors. A fingerprint is the
weighted mean of several such documents, L2-normalised, so the similarity
of a reviewer and a paper is the dot product of their fingerprints.

torch and the model are imported only when something has to be embedded,
so a run that reuses its cached fingerprints needs neither.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

BASE_MODEL = "allenai/specter2_base"
PROXIMITY_ADAPTER = "allenai/specter2"


def device() -> str:
    import torch
    return "cuda" if torch.cuda.is_available() else "cpu"


class Encoder:
    """The model, loaded once, turning documents into vectors."""

    def __init__(self) -> None:
        from adapters import AutoAdapterModel
        from transformers import AutoTokenizer
        where = device()
        print(f"Loading SPECTER2 on {where}...", file=sys.stderr)
        self.tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)
        model = AutoAdapterModel.from_pretrained(BASE_MODEL)
        model.load_adapter(PROXIMITY_ADAPTER, source="hf", set_active=True)
        self.model = model.to(where)
        self.model.eval()

    def doc(self, first: str, second: str = "") -> str:
        """A document in SPECTER2's trained shape: `title [SEP] abstract`."""
        return f"{first}{self.tokenizer.sep_token}{second}"

    def encode(self, texts: list[str], batch_size: int = 16) -> np.ndarray:
        """(N, 768) CLS embeddings of `texts`."""
        import torch
        where = next(self.model.parameters()).device
        chunks: list[np.ndarray] = []
        for start in range(0, len(texts), batch_size):
            inputs = self.tokenizer(texts[start:start + batch_size], padding=True,
                                    truncation=True, max_length=512, return_tensors="pt").to(where)
            with torch.no_grad():
                out = self.model(**inputs)
            chunks.append(out.last_hidden_state[:, 0, :].cpu().numpy())
        return np.concatenate(chunks, axis=0) if chunks else np.empty((0, 768), dtype=np.float32)

    def fingerprints(self, docs: list[list[tuple[str, float]]]) -> list[np.ndarray]:
        """One pooled vector per list of (document, weight)."""
        flat = [text for group in docs for text, _ in group]
        vectors = self.encode(flat)
        out, start = [], 0
        for group in docs:
            out.append(pool(vectors[start:start + len(group)], [w for _, w in group]))
            start += len(group)
        return out


def pool(vectors: np.ndarray, weights: list[float]) -> np.ndarray:
    """Weighted mean of `vectors`, L2-normalised."""
    w = np.asarray(weights, dtype=np.float64).reshape(-1, 1)
    mean = (vectors * w).sum(axis=0) / w.sum()
    norm = np.linalg.norm(mean)
    return mean / norm if norm > 0 else mean


def load_json(path: str) -> dict:
    """A JSON cache, {} when the file does not exist."""
    p = Path(path)
    if not p.exists():
        return {}
    with p.open(encoding="utf-8") as f:
        return json.load(f)


def save_json(data: dict, path: str) -> None:
    """Write a JSON cache atomically."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(p)
