"""SciFact dataset loading and claim grouping via connected components."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Literal

from amfv_eval.types import ClaimGroup, ScifactClaim

__all__ = ["load_claims", "load_corpus", "group_claims"]

_SPLIT_FILES: dict[str, str] = {
    "train": "claims_train.jsonl",
    "validation": "claims_dev.jsonl",
    "test": "claims_test.jsonl",
}
_CACHE_DIR = Path.home() / ".cache" / "amfv_eval" / "scifact"

_LABEL_MAP = {
    "SUPPORTS": "SUPPORTED",
    "SUPPORTED": "SUPPORTED",
    "CONTRADICT": "REFUTED",
    "REFUTED": "REFUTED",
    "NOT_ENOUGH_INFO": "NEI",
    "NEI": "NEI",
}


def load_claims(
    split: Literal["train", "validation", "test"],
    *,
    data_dir: Path | None = None,
) -> list[ScifactClaim]:
    """Load SciFact claims for a split.

    Args:
        split: One of ``train``, ``validation``, or ``test``.
        data_dir: Directory containing the SciFact JSONL files.  When
            provided the files are read directly from there without any
            network access.  When omitted the files are downloaded from
            GitHub and cached in ``~/.cache/amfv_eval/scifact/``.

    Returns:
        List of :class:`ScifactClaim` objects.
    """
    path = _local_path(split, data_dir) if data_dir else _cached_path(split)
    claims: list[ScifactClaim] = []
    for row in _iter_jsonl(path):
        evidence: dict = row.get("evidence") or {}
        cited_doc_ids: list[int] = [int(d) for d in (row.get("cited_doc_ids") or [])]
        label = _infer_label(evidence, row.get("label"))
        claims.append(
            ScifactClaim(
                id=int(row["id"]),
                claim=row["claim"],
                evidence=evidence,
                cited_doc_ids=cited_doc_ids,
                split=split,
                label=label,
            )
        )
    return claims


def load_corpus(data_dir: Path | None = None) -> dict[int, str]:
    """Load the SciFact corpus as a mapping from doc_id to full text.

    Text is ``title + " " + " ".join(abstract_sentences)``.

    Args:
        data_dir: Directory containing ``corpus.jsonl``.  Falls back to
            ``~/.cache/amfv_eval/scifact/corpus.jsonl`` (downloaded on demand
            alongside the claim splits if absent).
    """
    if data_dir is not None:
        path = Path(data_dir) / "corpus.jsonl"
        if not path.exists():
            raise FileNotFoundError(
                f"SciFact corpus not found at {path}. "
                "Expected filename: corpus.jsonl"
            )
    else:
        path = _CACHE_DIR / "corpus.jsonl"
        if not path.exists():
            _download_all_splits()
        if not path.exists():
            raise RuntimeError(
                f"SciFact corpus missing after download attempt. "
                f"Place corpus.jsonl manually at: {path}"
            )

    corpus: dict[int, str] = {}
    for row in _iter_jsonl(path):
        abstract = row.get("abstract") or []
        text = row.get("title", "") + " " + " ".join(abstract)
        corpus[int(row["doc_id"])] = text.strip()
    return corpus


def group_claims(claims: list[ScifactClaim]) -> list[ClaimGroup]:
    """Partition claims into connected components by shared cited doc IDs.

    Two claims belong to the same group if they share at least one cited
    document, transitively.  Claims with no cited documents form singleton
    groups and are excluded from the returned list (they cannot be fused).

    Args:
        claims: Claims from a single split.

    Returns:
        List of :class:`ClaimGroup` objects with two or more members,
        sorted by descending group size.
    """
    if not claims:
        return []
    return _buckets_to_groups(_bucket_by_parent(claims))


def _local_path(split: Literal["train", "validation", "test"], data_dir: Path) -> Path:
    """Resolve a split file from a user-supplied directory."""
    filename = _SPLIT_FILES[split]
    path = data_dir / filename
    if not path.exists():
        raise FileNotFoundError(
            f"SciFact {split} file not found at {path}. "
            f"Expected filename: {filename}"
        )
    return path


_SCIFACT_S3_URL = "https://scifact.s3.us-west-2.amazonaws.com/release/latest/data.tar.gz"


def _cached_path(split: Literal["train", "validation", "test"]) -> Path:
    """Return local path to the split file, downloading from S3 if absent."""
    filename = _SPLIT_FILES[split]
    local = _CACHE_DIR / filename
    if not local.exists():
        _download_all_splits()
    if not local.exists():
        raise RuntimeError(
            f"SciFact {split} split missing after download attempt. "
            f"Place the file manually at: {local}"
        )
    return local


def _download_all_splits() -> None:
    """Download all SciFact splits from AllenAI S3 into _CACHE_DIR."""
    import shutil
    import tarfile
    import tempfile
    import urllib.request

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Downloading SciFact from {_SCIFACT_S3_URL}…", flush=True)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tar_path = Path(tmp) / "data.tar.gz"
            urllib.request.urlretrieve(_SCIFACT_S3_URL, str(tar_path))
            with tarfile.open(tar_path) as tar:
                tar.extractall(tmp)
            # The tar contains data/claims_train.jsonl, data/claims_dev.jsonl,
            # data/corpus.jsonl, etc.
            data_dir = Path(tmp) / "data"
            for dest_name in (*_SPLIT_FILES.values(), "corpus.jsonl"):
                src = data_dir / dest_name
                if src.exists():
                    shutil.copy2(src, _CACHE_DIR / dest_name)
    except Exception as e:
        raise RuntimeError(
            f"Could not download SciFact from {_SCIFACT_S3_URL}. "
            f"Place the files manually in: {_CACHE_DIR}"
        ) from e


def _iter_jsonl(path: Path):
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def _bucket_by_parent(claims: list[ScifactClaim]) -> dict[int, list[ScifactClaim]]:
    claim_ids = [c.id for c in claims]
    parent: dict[int, int] = {cid: cid for cid in claim_ids}

    def find_parent(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]  # path compression
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        px, py = find_parent(x), find_parent(y)
        if px != py:
            parent[px] = py

    doc_to_claims: dict[int, list[int]] = defaultdict(list)
    for c in claims:
        for doc_id in c.cited_doc_ids:
            doc_to_claims[doc_id].append(c.id)

    for members in doc_to_claims.values():
        for i in range(1, len(members)):
            union(members[0], members[i])

    buckets: dict[int, list[ScifactClaim]] = defaultdict(list)
    by_id = {c.id: c for c in claims}
    for cid in claim_ids:
        buckets[find_parent(cid)].append(by_id[cid])

    return buckets


def _buckets_to_groups(buckets: dict[int, list[ScifactClaim]]) -> list[ClaimGroup]:
    groups: list[ClaimGroup] = []
    for members in buckets.values():
        if len(members) < 2:
            continue
        all_doc_ids: frozenset[int] = frozenset(
            doc_id for c in members for doc_id in c.cited_doc_ids
        )
        split = members[0].split
        groups.append(ClaimGroup(claims=tuple(members), doc_ids=all_doc_ids, split=split))
    groups.sort(key=lambda g: len(g.claims), reverse=True)
    return groups


def _infer_label(
    evidence: dict,
    raw_label: str | None,
) -> Literal["SUPPORTED", "REFUTED", "NEI"]:
    if raw_label:
        mapped = _LABEL_MAP.get(raw_label.upper())
        if mapped:
            return mapped  # type: ignore[return-value]

    if not evidence:
        return "NEI"

    labels: list[str] = []
    for evs in evidence.values():
        if isinstance(evs, list):
            for ev in evs:
                if isinstance(ev, dict) and "label" in ev:
                    labels.append(ev["label"].upper())

    if not labels:
        return "NEI"

    if any(lb in ("CONTRADICT", "REFUTED") for lb in labels):
        return "REFUTED"
    return "SUPPORTED"
