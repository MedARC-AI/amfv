"""Embed SciFact source documents and identify semantically similar claim pairs.

For each cited document, we compute a dense embedding (mean-pool + L2 norm).
Two claims are considered similar if any of claim A's cited docs has cosine
similarity ≥ threshold with any of claim B's cited docs (max over cross-pairs).
The precomputed ``similar_pairs`` output maps claim_id → [similar_claim_ids]
and is consumed by the generation step to avoid fusing topically redundant claims.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Literal

__all__ = ["embed_and_find_similar"]


def embed_and_find_similar(
    splits: list[Literal["train", "validation", "test"]],
    *,
    model_name: str,
    threshold: float,
    out: Path,
    data_dir: Path | None = None,
    print_histogram: bool = False,
) -> None:
    """Embed cited documents and save similar claim pairs to *out*.

    Similarity between two claims is the **max** cosine similarity over all
    (doc_a, doc_b) cross-pairs where doc_a is cited by claim A and doc_b is
    cited by claim B.  This avoids the dilution effect of mean pooling and
    directly answers "do these two claims draw on the same evidence?"

    Output JSON schema::

        {
          "model": "<model_name>",
          "embed_target": "documents",
          "similarity": "max",
          "threshold": 0.85,
          "n_docs_embedded": 3145,
          "n_claims": 1109,
          "histogram": [{"bin": [-1.0, -0.9], "count": 0}, ...],
          "similar_pairs": {"<claim_id>": [<similar_id>, ...], ...}
        }

    The histogram covers pairwise document similarities (upper triangle of the
    doc×doc matrix) in 20 bins of width 0.1 over [-1, 1].  It is always saved
    and optionally printed to stdout.
    """
    import torch
    from transformers import AutoModel, AutoTokenizer

    from amfv_eval.scifact import load_claims, load_corpus

    # ------------------------------------------------------------------
    # Load claims and collect cited doc IDs
    # ------------------------------------------------------------------
    all_claims = []
    for split in splits:
        all_claims.extend(load_claims(split, data_dir=data_dir))

    n_claims = len(all_claims)
    claim_ids = [c.id for c in all_claims]
    cited_per_claim: list[list[int]] = [c.cited_doc_ids for c in all_claims]

    needed_doc_ids: set[int] = set()
    for ids in cited_per_claim:
        needed_doc_ids.update(ids)

    print(
        f"[embed] {n_claims} claims across splits {splits}, "
        f"citing {len(needed_doc_ids)} distinct documents.",
        flush=True,
    )

    # ------------------------------------------------------------------
    # Load corpus and embed only cited documents
    # ------------------------------------------------------------------
    print(f"[embed] Loading corpus…", flush=True)
    corpus = load_corpus(data_dir=data_dir)
    cited_corpus = {did: text for did, text in corpus.items() if did in needed_doc_ids}
    missing = needed_doc_ids - cited_corpus.keys()
    if missing:
        print(f"[embed] WARNING: {len(missing)} cited doc IDs not found in corpus.", flush=True)

    doc_ids_ordered = sorted(cited_corpus.keys())
    texts = [cited_corpus[d] for d in doc_ids_ordered]
    doc_id_to_idx: dict[int, int] = {d: i for i, d in enumerate(doc_ids_ordered)}
    n_docs = len(texts)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[embed] Loading {model_name} on {device}…", flush=True)
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModel.from_pretrained(model_name).to(device)
    model.eval()

    print(f"[embed] Embedding {n_docs} documents…", flush=True)
    doc_embeddings = _embed_texts(texts, tokenizer, model, device)  # [n_docs, d]

    # ------------------------------------------------------------------
    # Pairwise document similarity and histogram
    # ------------------------------------------------------------------
    print(f"[embed] Computing {n_docs}×{n_docs} doc similarity matrix…", flush=True)
    doc_sims = (doc_embeddings @ doc_embeddings.T).cpu()  # [n_docs, n_docs]

    didx = torch.triu_indices(n_docs, n_docs, offset=1)
    upper_docs = doc_sims[didx[0], didx[1]]

    histogram = _build_histogram(upper_docs)
    histogram_zoomed = _build_zoomed_histogram(upper_docs)
    if print_histogram:
        _print_histogram(histogram)
        _print_zoomed_histogram(histogram_zoomed)

    # ------------------------------------------------------------------
    # Translate doc-level similar pairs → claim-level similar_pairs (max)
    #
    # For each doc pair (da, db) with sim ≥ threshold, every claim citing da
    # is similar to every claim citing db.
    # ------------------------------------------------------------------
    doc_to_claims: dict[int, list[int]] = defaultdict(list)
    for claim_idx, doc_ids in enumerate(cited_per_claim):
        for did in doc_ids:
            if did in doc_id_to_idx:
                doc_to_claims[did].append(claim_ids[claim_idx])

    mask = upper_docs >= threshold
    n_similar_doc_pairs = int(mask.sum().item())
    print(
        f"[embed] {n_similar_doc_pairs} similar document pairs above threshold {threshold}.",
        flush=True,
    )

    similar_pairs: dict[str, list[int]] = {}
    for idx_a, idx_b in zip(didx[0][mask].tolist(), didx[1][mask].tolist()):
        da, db = doc_ids_ordered[idx_a], doc_ids_ordered[idx_b]
        for ca in doc_to_claims.get(da, []):
            for cb in doc_to_claims.get(db, []):
                if ca != cb:
                    similar_pairs.setdefault(str(ca), []).append(cb)
                    similar_pairs.setdefault(str(cb), []).append(ca)

    # Deduplicate within each list
    similar_pairs = {k: list(dict.fromkeys(v)) for k, v in similar_pairs.items()}

    n_claim_pairs = sum(len(v) for v in similar_pairs.values()) // 2
    print(f"[embed] {n_claim_pairs} similar claim pairs derived.", flush=True)

    result = {
        "model": model_name,
        "embed_target": "documents",
        "similarity": "max",
        "threshold": threshold,
        "n_docs_embedded": n_docs,
        "n_claims": n_claims,
        "histogram": histogram,
        "histogram_zoomed": histogram_zoomed,
        "similar_pairs": similar_pairs,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(f"[embed] Saved → {out}", flush=True)


def _embed_texts(texts, tokenizer, model, device: str, batch_size: int = 64):
    import torch
    import torch.nn.functional as F

    all_embeddings = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        encoded = tokenizer(
            batch, padding=True, truncation=True, max_length=512, return_tensors="pt"
        )
        encoded = {k: v.to(device) for k, v in encoded.items()}
        with torch.no_grad():
            output = model(**encoded)
        attn = encoded["attention_mask"].unsqueeze(-1).float()
        pooled = (output.last_hidden_state * attn).sum(dim=1) / attn.sum(dim=1)
        pooled = F.normalize(pooled, p=2, dim=-1)
        all_embeddings.append(pooled.cpu())
        if (i // batch_size) % 5 == 0:
            done = min(i + batch_size, len(texts))
            print(f"[embed]   {done}/{len(texts)} docs", flush=True)
    return torch.cat(all_embeddings, dim=0)


def _build_histogram(upper_triangle) -> list[dict]:
    """20 bins of width 0.1 covering [-1, 1]."""
    bins = [round(-1.0 + i * 0.1, 1) for i in range(21)]
    histogram = []
    for k in range(len(bins) - 1):
        lo, hi = bins[k], bins[k + 1]
        if k < len(bins) - 2:
            count = int(((upper_triangle >= lo) & (upper_triangle < hi)).sum().item())
        else:
            count = int((upper_triangle >= lo).sum().item())
        histogram.append({"bin": [lo, hi], "count": count})
    return histogram


def _build_zoomed_histogram(upper_triangle) -> list[dict]:
    """15 bins of width 0.02 covering [0.7, 1.0]."""
    bins = [round(0.7 + i * 0.02, 2) for i in range(16)]
    histogram = []
    for k in range(len(bins) - 1):
        lo, hi = bins[k], bins[k + 1]
        if k < len(bins) - 2:
            count = int(((upper_triangle >= lo) & (upper_triangle < hi)).sum().item())
        else:
            count = int((upper_triangle >= lo).sum().item())
        histogram.append({"bin": [lo, hi], "count": count})
    return histogram


def _print_histogram(histogram: list[dict]) -> None:
    max_count = max(h["count"] for h in histogram) or 1
    bar_width = 40
    print("\nDocument cosine similarity distribution (pairwise upper triangle):")
    print(f"  {'Bin':<14}  {'Count':>8}  Bar")
    print(f"  {'-'*14}  {'-'*8}  {'-'*bar_width}")
    for h in histogram:
        lo, hi = h["bin"]
        count = h["count"]
        bar = "█" * int(count / max_count * bar_width)
        print(f"  [{lo:+.1f}, {hi:+.1f})  {count:>8,}  {bar}")
    print()


def _print_zoomed_histogram(histogram: list[dict]) -> None:
    max_count = max(h["count"] for h in histogram) or 1
    bar_width = 40
    print("Document cosine similarity distribution (> 0.70, bins of 0.02):")
    print(f"  {'Bin':<16}  {'Count':>8}  Bar")
    print(f"  {'-'*16}  {'-'*8}  {'-'*bar_width}")
    for h in histogram:
        lo, hi = h["bin"]
        count = h["count"]
        bar = "█" * int(count / max_count * bar_width)
        print(f"  [{lo:.2f}, {hi:.2f})  {count:>8,}  {bar}")
    print()
