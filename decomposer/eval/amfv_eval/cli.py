"""CLI entry point for decomposer eval-set generation, filtering, and evaluation.

Commands
--------
``amfv-eval generate``   Generate passages from SciFact and validate dataset quality.
``amfv-eval filter``     Re-validate with a different judge model.
``amfv-eval decompose``  Run decomposition on a dataset with the model under test.
``amfv-eval eval``       Judge decomposition results with a (potentially different) model.
``amfv-eval shard``      Split a JSONL into N shards for parallel processing.
``amfv-eval merge``      Merge shard outputs back into a single JSONL.

All commands accept ``--config config.yaml`` to load defaults from a YAML file.
List-type commands (filter, decompose, eval) also accept ``--step N`` to select
which entry in the YAML list to use.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import signal
import sys
from pathlib import Path
from typing import Generator, Literal

import openai
import yaml
from tqdm import tqdm

from amfv_eval._cache import GenerationCache
from amfv_eval._decompose import DecomposeError, decompose
from amfv_eval._judge import (
    JudgeError,
    judge_atoms,
    judge_claim_consistency,
    judge_context_extraction,
    judge_operator_awareness,
    judge_operators,
    judge_source_claims,
)
from amfv_eval.generate import generate_split
from amfv_eval.metrics import compute_metrics, print_report
from amfv_eval.scifact import group_claims, load_claims

_DEFAULT_BASE_URL = "http://localhost:8000/v1"
_MAX_DECOMPOSE_ATTEMPTS = 3

# Base directory for resolving relative paths from YAML config.
# Set via the DIR environment variable, e.g. DIR=/mount/marii python slurm/pipeline.py …
_BASE_DIR = Path(os.environ.get("DIR", ".")).resolve()


def _resolve_path(val: str | Path | None) -> Path | None:
    """Resolve a path against DIR if it is relative; absolute paths are unchanged."""
    if val is None:
        return None
    p = Path(val)
    return p if p.is_absolute() else _BASE_DIR / p

# ---------------------------------------------------------------------------
# Graceful shutdown on SIGTERM
# ---------------------------------------------------------------------------

_shutdown = False


def _handle_sigterm(signum: int, frame: object) -> None:
    global _shutdown
    _shutdown = True
    print(
        "[amfv-eval] SIGTERM received; finishing current item then exiting…",
        file=sys.stderr, flush=True,
    )


signal.signal(signal.SIGTERM, _handle_sigterm)

# ---------------------------------------------------------------------------
# YAML config loading
# ---------------------------------------------------------------------------


def _load_yaml_defaults(config_path: Path, command: str, step: int | None) -> dict:
    """Load CLI defaults from a YAML config for the given command and step."""
    with config_path.open() as f:
        cfg = yaml.safe_load(f)

    # Shared top-level settings
    defaults: dict = {}
    for key in ("base_url", "cache_dir", "debug", "max_retries"):
        if key in cfg:
            val = cfg[key]
            if key == "cache_dir" and val is not None:
                val = _resolve_path(val)
            defaults[key] = val

    # Command-specific settings
    yaml_key = command.replace("-", "_")
    cmd_cfg = cfg.get(yaml_key)
    if cmd_cfg is None:
        return defaults

    if isinstance(cmd_cfg, list):
        if step is None or step >= len(cmd_cfg):
            return defaults
        cmd_cfg = cmd_cfg[step]

    path_keys = {"out", "failures", "input", "cache_dir", "out_dir", "in_dir", "data_dir", "similar_pairs"}
    for key, val in cmd_cfg.items():
        if key in path_keys and val is not None:
            val = _resolve_path(val)
        defaults[key] = val

    return defaults


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


def _write_jsonl(records: list[dict], path: Path) -> None:
    with path.open("w") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _load_jsonl(path: Path) -> list[dict]:
    with path.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def _ensure_parent(path: Path | None) -> None:
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)


def _load_completed_ids(path: Path | None) -> set[str]:
    """Return the set of record IDs already written to an output file."""
    if path is None or not path.exists():
        return set()
    with path.open() as f:
        ids = set()
        for line in f:
            line = line.strip()
            if line:
                try:
                    ids.add(json.loads(line)["id"])
                except (json.JSONDecodeError, KeyError):
                    pass
        return ids


def _normalise_base_url(url: str) -> str:
    if "://" not in url:
        url = "http://" + url
    for suffix in ("/v1/chat/completions", "/v1/completions", "/v1/embeddings"):
        if url.endswith(suffix):
            url = url[: -len(suffix) + 3]
            break
    return url


# ---------------------------------------------------------------------------
# Decomposition and judging
# ---------------------------------------------------------------------------


def _decompose_examples(
    examples: list[dict],
    *,
    client: openai.OpenAI,
    model: str,
    cache: GenerationCache,
    enable_thinking: bool = False,
    debug: bool = False,
) -> Generator[dict, None, None]:
    """Decompose each passage. Yields records; checks _shutdown between items."""
    for ex in tqdm(examples, desc="decomposing", unit="ex"):
        record = dict(ex)
        record.setdefault("decompositions", [])

        last_error: DecomposeError | None = None
        for attempt in range(_MAX_DECOMPOSE_ATTEMPTS):
            try:
                atoms, trace = decompose(
                    ex["passage"], client=client, model=model, cache=cache,
                    enable_thinking=enable_thinking, debug=debug,
                )
                record["decompositions"].append({
                    "model": model,
                    "extracted_atoms": atoms,
                    "thinking_trace": trace,
                })
                last_error = None
                break
            except DecomposeError as e:
                last_error = e
                print(
                    f"[WARNING] decompose attempt {attempt + 1}/{_MAX_DECOMPOSE_ATTEMPTS}"
                    f" failed for {ex.get('id', '?')}: {e}",
                    file=sys.stderr, flush=True,
                )

        if last_error is not None:
            record["decompositions"].append({
                "model": model,
                "extracted_atoms": [],
                "thinking_trace": "",
                "error": {
                    "exception": last_error.exception_type,
                    "finish_reason": last_error.finish_reason,
                    "attempts": _MAX_DECOMPOSE_ATTEMPTS,
                },
            })

        yield record
        if _shutdown:
            break


def _judge_examples(
    examples: list[dict],
    *,
    client: openai.OpenAI,
    model: str,
    cache: GenerationCache,
) -> Generator[dict, None, None]:
    """Judge the latest decomposition in each record. Yields records."""
    for ex in tqdm(examples, desc="judging", unit="ex"):
        decomps = ex.get("decompositions", [])
        if not decomps:
            print(
                f"[WARNING] record {ex.get('id', '?')} has no decompositions; skipping.",
                file=sys.stderr, flush=True,
            )
            continue
        latest = decomps[-1]
        atoms: list[str] = latest["extracted_atoms"]
        trace: str = latest["thinking_trace"]

        judge_errors: list[dict] = []

        atom_covered = None
        try:
            atom_covered = judge_atoms(
                ex["required_atoms"], atoms, client=client, model=model, cache=cache
            )
        except JudgeError as e:
            judge_errors.append({"judge": "judge_atoms", "exception": e.exception_type, "finish_reason": e.finish_reason})

        context_extracted = None
        try:
            context_extracted = judge_context_extraction(
                ex.get("context_sentences", []), atoms, client=client, model=model, cache=cache
            )
        except JudgeError as e:
            judge_errors.append({"judge": "judge_context_extraction", "exception": e.exception_type, "finish_reason": e.finish_reason})

        operators_noticed = None
        try:
            operators_noticed = judge_operator_awareness(
                ex["operators"], trace, client=client, model=model, cache=cache
            )
        except JudgeError as e:
            judge_errors.append({"judge": "judge_operator_awareness", "exception": e.exception_type, "finish_reason": e.finish_reason})

        passed = (
            not judge_errors
            and bool(atom_covered) and all(atom_covered)
            and not any(context_extracted or [])
        )

        n_gold = len(atom_covered) if atom_covered is not None else 0
        recall = sum(atom_covered) / n_gold if (atom_covered is not None and n_gold) else (1.0 if atom_covered is not None else None)
        n_ctx = len(context_extracted) if context_extracted is not None else 0
        ctx_precision = (1.0 - sum(context_extracted) / n_ctx) if (context_extracted is not None and n_ctx) else (1.0 if context_extracted is not None else None)
        op_awareness = (
            sum(operators_noticed.values()) / len(operators_noticed)
            if operators_noticed else (1.0 if operators_noticed is not None else None)
        )

        eval_score = (0.6 * recall + 0.4 * ctx_precision) if recall is not None and ctx_precision is not None else None
        reward_score = (
            0.5 * recall + 0.3 * ctx_precision + 0.2 * op_awareness
            if recall is not None and ctx_precision is not None and op_awareness is not None
            else None
        )

        evaluation: dict = {
            "decomposition_model": latest["model"],
            "judge_model": model,
            "passed": passed,
            "judge_errors": judge_errors,
        }
        if atom_covered is not None:
            evaluation["atom_covered"] = atom_covered
            evaluation["atom_recall"] = recall
        if context_extracted is not None:
            evaluation["context_extracted"] = context_extracted
            evaluation["context_precision"] = ctx_precision
        if operators_noticed is not None:
            evaluation["operators_noticed"] = operators_noticed
            evaluation["operator_awareness_score"] = op_awareness
        if eval_score is not None:
            evaluation["eval_score"] = eval_score
        if reward_score is not None:
            evaluation["reward_score"] = reward_score

        record = dict(ex)
        record.setdefault("evaluations", [])
        record["evaluations"].append(evaluation)
        record["passed"] = passed
        yield record
        if _shutdown:
            break


# ---------------------------------------------------------------------------
# Dataset validation
# ---------------------------------------------------------------------------


def _validate_dataset_examples(
    examples: list[dict],
    *,
    client: openai.OpenAI,
    model: str,
    cache: GenerationCache,
) -> tuple[list[dict], list[dict]]:
    """Verify each example meets dataset quality requirements.

    Returns:
        ``(passed, failed)`` — both lists have a ``"dataset_validation"`` field.
    """
    passed: list[dict] = []
    failed: list[dict] = []
    for ex in tqdm(examples, desc="validating dataset", unit="ex"):
        source_claims = [c["claim"] for c in ex.get("source_claims", [])]
        stats: dict = {}

        judge_errors: list[dict] = []

        source_coverage = None
        try:
            source_coverage = judge_source_claims(
                source_claims, ex["passage"], ex["required_atoms"],
                client=client, model=model, cache=cache, stats=stats,
            )
        except JudgeError as e:
            judge_errors.append({"judge": "judge_source_claims", "exception": e.exception_type, "finish_reason": e.finish_reason})

        operators_applied = None
        try:
            operators_applied = judge_operators(
                ex["operators"], ex["passage"],
                client=client, model=model, cache=cache, stats=stats,
            )
        except JudgeError as e:
            judge_errors.append({"judge": "judge_operators", "exception": e.exception_type, "finish_reason": e.finish_reason})

        claims_consistent: bool | None = None
        try:
            claims_consistent = judge_claim_consistency(
                source_claims, client=client, model=model, cache=cache,
            )
        except JudgeError as e:
            judge_errors.append({"judge": "judge_claim_consistency", "exception": e.exception_type, "finish_reason": e.finish_reason})

        valid = (
            not judge_errors
            and source_coverage is not None
            and all(source_coverage["in_passage"])
            and all(source_coverage["in_gold"])
            and operators_applied is not None
            and all(operators_applied.values())
            and claims_consistent is True
        )

        record = dict(ex)
        validation: dict = {
            "model": model,
            "passed": valid,
            "judge_errors": judge_errors,
        }
        if source_coverage is not None:
            validation["source_in_passage"] = source_coverage["in_passage"]
            validation["source_in_gold"] = source_coverage["in_gold"]
        if operators_applied is not None:
            validation["operators_applied"] = operators_applied
        if claims_consistent is not None:
            validation["claims_consistent"] = claims_consistent
        if not valid and stats:
            validation["thinking_chars"] = stats.get("thinking_chars", 0)
            validation["judge_responses"] = stats.get("responses", [])
        record["dataset_validation"] = validation
        (passed if valid else failed).append(record)
        if _shutdown:
            break

    return passed, failed


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def _cmd_embed_claims(args: argparse.Namespace) -> None:
    from amfv_eval._embed import embed_and_find_similar

    if args.out is None:
        sys.exit("amfv-eval embed-claims: error: --out is required (or set via --config)")
    _ensure_parent(args.out)

    embed_and_find_similar(
        args.splits,
        model_name=args.model,
        threshold=args.threshold,
        out=args.out,
        data_dir=args.data_dir,
        print_histogram=args.print_histogram,
    )


def _load_similar_pairs(path: Path) -> dict[int, frozenset[int]]:
    data = json.loads(path.read_text())
    return {
        int(k): frozenset(v for v in vs)
        for k, vs in data.get("similar_pairs", {}).items()
    }


def _cmd_generate(args: argparse.Namespace) -> None:
    if args.model is None:
        sys.exit("amfv-eval generate: error: --model is required (or set via --config)")
    if args.out is None:
        sys.exit("amfv-eval generate: error: --out is required (or set via --config)")
    _ensure_parent(args.out)

    client = openai.OpenAI(base_url=_normalise_base_url(args.base_url), api_key="EMPTY", max_retries=args.max_retries)
    cache = GenerationCache(cache_dir=args.cache_dir)

    similar_pairs: dict[int, frozenset[int]] | None = None
    similar_pairs_path: Path | None = getattr(args, "similar_pairs", None)
    if similar_pairs_path is not None and similar_pairs_path.exists():
        print(f"Loading embedding similar pairs from {similar_pairs_path}…", flush=True)
        similar_pairs = _load_similar_pairs(similar_pairs_path)
        print(f"  {len(similar_pairs):,} claims have similar neighbors.", flush=True)
    elif similar_pairs_path is not None:
        print(f"[WARNING] similar_pairs path not found ({similar_pairs_path}); proceeding without it.", file=sys.stderr, flush=True)

    all_examples: list[dict] = []

    for split in args.splits:
        split_key: Literal["train", "validation", "test"] = split  # type: ignore[assignment]
        print(f"\n[{split}] Loading SciFact claims…", flush=True)
        claims = load_claims(split_key, data_dir=args.data_dir)
        n_claims = len(claims)
        groups = group_claims(claims)
        target = args.n_examples if args.n_examples is not None else n_claims * 4
        print(
            f"[{split}] {n_claims} claims · {len(groups)} groups ≥2 "
            f"· target {target} examples",
            flush=True,
        )

        print(f"[{split}] Generating passages…", flush=True)
        examples = generate_split(
            groups, n_claims=n_claims, split=split_key,
            client=client, model=args.model, cache=cache,
            seed=args.seed, target=target,
            concurrency=args.concurrency, debug=args.debug,
            similar_pairs=similar_pairs,
        )
        all_examples.extend(ex.to_dict() for ex in examples)
        print(f"[{split}] {len(examples)} passages generated.", flush=True)

    _write_jsonl(all_examples, args.out)
    print(f"Generated: {len(all_examples):,} → {args.out}")


def _cmd_filter(args: argparse.Namespace) -> None:
    if args.model is None:
        sys.exit("amfv-eval filter: error: --model is required (or set via --config)")
    if args.input is None:
        sys.exit("amfv-eval filter: error: --input is required (or set via --config)")
    if args.out is None:
        sys.exit("amfv-eval filter: error: --out is required (or set via --config)")
    _ensure_parent(args.out)
    _ensure_parent(args.failures)

    examples = _load_jsonl(args.input)
    print(f"Loaded {len(examples):,} examples from {args.input}", flush=True)

    client = openai.OpenAI(base_url=_normalise_base_url(args.base_url), api_key="EMPTY", max_retries=args.max_retries)
    cache = GenerationCache(cache_dir=args.cache_dir)

    print(f"Validating dataset with {args.model}…", flush=True)
    passed, failed = _validate_dataset_examples(
        examples, client=client, model=args.model, cache=cache
    )
    print(f"{len(passed)}/{len(examples)} passed dataset validation.", flush=True)

    _write_jsonl(passed, args.out)
    print(f"Passed : {len(passed):,} → {args.out}")
    if args.failures:
        _write_jsonl(failed, args.failures)
        print(f"Failed : {len(failed):,} → {args.failures}")


def _cmd_decompose(args: argparse.Namespace) -> None:
    if args.model is None:
        sys.exit("amfv-eval decompose: error: --model is required (or set via --config)")
    if args.input is None:
        sys.exit("amfv-eval decompose: error: --input is required (or set via --config)")
    if args.out is None:
        sys.exit("amfv-eval decompose: error: --out is required (or set via --config)")
    _ensure_parent(args.out)

    examples = _load_jsonl(args.input)
    print(f"Loaded {len(examples):,} examples from {args.input}", flush=True)

    completed = _load_completed_ids(args.out)
    if completed:
        print(f"Resuming: {len(completed):,} already done, {len(examples) - len(completed):,} remaining.", flush=True)
    to_process = [ex for ex in examples if ex.get("id") not in completed]

    client = openai.OpenAI(base_url=_normalise_base_url(args.base_url), api_key="EMPTY", max_retries=args.max_retries)
    cache = GenerationCache(cache_dir=args.cache_dir)

    print(f"Decomposing with {args.model}…", flush=True)
    with args.out.open("a") as f:
        for record in _decompose_examples(
            to_process, client=client, model=args.model, cache=cache,
            enable_thinking=args.enable_thinking, debug=args.debug,
        ):
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()

    total = len(completed) + len(to_process)
    print(f"Decomposed : {total:,} → {args.out}")


def _cmd_eval(args: argparse.Namespace) -> None:
    if args.model is None:
        sys.exit("amfv-eval eval: error: --model is required (or set via --config)")
    if args.input is None:
        sys.exit("amfv-eval eval: error: --input is required (or set via --config)")
    if args.out is None:
        sys.exit("amfv-eval eval: error: --out is required (or set via --config)")
    _ensure_parent(args.out)
    _ensure_parent(args.failures)

    examples = _load_jsonl(args.input)
    print(f"Loaded {len(examples):,} examples from {args.input}", flush=True)

    completed = _load_completed_ids(args.out)
    if completed:
        print(f"Resuming: {len(completed):,} already done, {len(examples) - len(completed):,} remaining.", flush=True)
    to_process = [ex for ex in examples if ex.get("id") not in completed]

    client = openai.OpenAI(base_url=_normalise_base_url(args.base_url), api_key="EMPTY", max_retries=args.max_retries)
    cache = GenerationCache(cache_dir=args.cache_dir)

    print(f"Judging with {args.model}…", flush=True)
    with args.out.open("a") as f:
        for record in _judge_examples(
            to_process, client=client, model=args.model, cache=cache,
        ):
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()

    if not _shutdown:
        all_records = _load_jsonl(args.out)
        passed = [r for r in all_records if r.get("passed")]
        failed = [r for r in all_records if not r.get("passed")]
        print_report(compute_metrics(all_records), title=f"eval · {args.model}")
        if args.failures:
            _write_jsonl(failed, args.failures)
        print(f"Passed : {len(passed):,}  Failed : {len(failed):,} → {args.out}")


def _cmd_shard(args: argparse.Namespace) -> None:
    examples = _load_jsonl(args.input)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    shard_size = math.ceil(len(examples) / args.n)
    written = 0
    for i in range(args.n):
        shard = examples[i * shard_size : (i + 1) * shard_size]
        if shard:
            _write_jsonl(shard, args.out_dir / f"shard_{i:03d}.jsonl")
            written += 1
    print(f"Split {len(examples):,} examples into {written} shards in {args.out_dir}")


def _cmd_merge(args: argparse.Namespace) -> None:
    shard_files = sorted(args.in_dir.glob("shard_*.jsonl"))
    if not shard_files:
        print(f"No shard files found in {args.in_dir}", file=sys.stderr)
        sys.exit(1)
    all_records: list[dict] = []
    for path in shard_files:
        all_records.extend(_load_jsonl(path))
    _ensure_parent(args.out)
    _write_jsonl(all_records, args.out)
    print(f"Merged {len(shard_files)} shards → {len(all_records):,} records → {args.out}")


def _cmd_report(args: argparse.Namespace) -> None:
    records = _load_jsonl(args.input)
    print(f"Loaded {len(records):,} records from {args.input}", flush=True)
    metrics = compute_metrics(records)
    print_report(metrics, title=f"Eval report — {args.input.name}")
    if args.out:
        _ensure_parent(args.out)
        args.out.write_text(json.dumps(metrics, indent=2, ensure_ascii=False) + "\n")
        print(f"Metrics written to {args.out}")


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _add_common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--config", type=Path, default=None, help="YAML config file.")
    p.add_argument("--model", default=None, help="Model name as registered in the vLLM server.")
    p.add_argument(
        "--base-url",
        default=_DEFAULT_BASE_URL,
        help=f"vLLM OpenAI-compatible endpoint (default: {_DEFAULT_BASE_URL}).",
    )
    p.add_argument("--cache-dir", type=Path, default=None, help="LLM output cache directory.")
    p.add_argument("--out", type=Path, default=None, help="Output JSONL for passing examples.")
    p.add_argument("--failures", type=Path, default=None, help="Output JSONL for failing examples (optional).")
    p.add_argument("--debug", action="store_true", default=False, help="Print each chat completion response.")
    p.add_argument("--max-retries", type=int, default=2, help="OpenAI client retry limit on transient errors (default: 2).")


def _add_input_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument("--input", "-i", type=Path, default=None, help="Input JSONL.")


def _add_step_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--step", type=int, default=None,
        help="Index into a YAML list section (filter/decompose/eval) to select.",
    )


def main(argv: list[str] | None = None) -> None:
    """Generate, filter, decompose, and evaluate decomposer eval examples."""

    # Pre-parse to extract --config and --step for YAML default loading.
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", type=Path, default=None)
    pre.add_argument("--step", type=int, default=None)
    pre.add_argument("command", nargs="?")
    pre_args, _ = pre.parse_known_args(argv)

    parser = argparse.ArgumentParser(
        prog="amfv-eval",
        description="Decomposer eval-set generation, filtering, and model evaluation.",
    )
    subs = parser.add_subparsers(dest="command", required=True)

    # embed-claims
    emb = subs.add_parser("embed-claims", help="Embed SciFact claims and precompute similar pairs.")
    emb.add_argument("--config", type=Path, default=None, help="YAML config file.")
    emb.add_argument("--out", type=Path, default=None, help="Output JSON file for similar pairs.")
    emb.add_argument(
        "--model", default="Simonlee711/Clinical_ModernBERT",
        help="HuggingFace model for embedding (default: Simonlee711/Clinical_ModernBERT).",
    )
    emb.add_argument(
        "--threshold", type=float, default=0.85,
        help="Cosine similarity threshold above which a pair is considered similar (default: 0.85).",
    )
    emb.add_argument(
        "--splits", nargs="+", choices=["train", "validation", "test"],
        default=["train", "validation", "test"],
    )
    emb.add_argument("--data-dir", type=Path, default=None, help="Directory containing SciFact JSONL files.")
    emb.add_argument("--print-histogram", action="store_true", default=False, help="Print cosine similarity histogram to stdout.")

    # generate
    gen = subs.add_parser("generate", help="Generate passages from SciFact and validate dataset quality.")
    _add_common_args(gen)
    gen.add_argument("--seed", type=int, default=42, help="Random seed (default: 42).")
    gen.add_argument("--splits", nargs="+", choices=["train", "validation", "test"], default=["train", "validation", "test"])
    gen.add_argument("--data-dir", type=Path, default=None, help="Directory containing SciFact JSONL files.")
    gen.add_argument("--n-examples", type=int, default=None, help="Examples to generate per split (default: n_claims × 4).")
    gen.add_argument("--concurrency", type=int, default=8, help="Concurrent requests to the vLLM server (default: 8).")

    # filter
    filt = subs.add_parser("filter", help="Re-validate an existing dataset with a different judge model.")
    _add_common_args(filt)
    _add_input_arg(filt)
    _add_step_arg(filt)

    # decompose
    dec = subs.add_parser("decompose", help="Decompose passages using the model under test.")
    _add_common_args(dec)
    _add_input_arg(dec)
    _add_step_arg(dec)
    dec.add_argument("--enable-thinking", action="store_true", default=False, help="Pass enable_thinking=True to vLLM.")

    # eval
    ev = subs.add_parser("eval", help="Judge decomposition results with a (potentially different) model.")
    _add_common_args(ev)
    _add_input_arg(ev)
    _add_step_arg(ev)

    # shard
    sh = subs.add_parser("shard", help="Split a JSONL into N shards for parallel processing.")
    sh.add_argument("--input", "-i", type=Path, required=True)
    sh.add_argument("--n", type=int, required=True, help="Number of shards.")
    sh.add_argument("--out-dir", type=Path, required=True)

    # merge
    mg = subs.add_parser("merge", help="Merge shard outputs back into a single JSONL.")
    mg.add_argument("--in-dir", type=Path, required=True)
    mg.add_argument("--out", type=Path, required=True)

    # report
    rp = subs.add_parser("report", help="Compute and print metrics from a merged eval results JSONL.")
    rp.add_argument("--input", "-i", type=Path, required=True, help="Merged eval results JSONL.")
    rp.add_argument("--out", type=Path, default=None, help="Write metrics as JSON to this path.")

    # Apply YAML defaults to the relevant subparser before the full parse.
    subparser_map = {
        "embed-claims": emb, "generate": gen, "filter": filt, "decompose": dec,
        "eval": ev, "shard": sh, "merge": mg,
    }
    if pre_args.config and pre_args.command in subparser_map:
        try:
            yaml_defaults = _load_yaml_defaults(pre_args.config, pre_args.command, pre_args.step)
            subparser_map[pre_args.command].set_defaults(**yaml_defaults)
        except Exception as e:
            print(f"[WARNING] Could not load YAML config: {e}", file=sys.stderr)

    args = parser.parse_args(argv)
    {
        "embed-claims": _cmd_embed_claims,
        "generate": _cmd_generate,
        "filter": _cmd_filter,
        "decompose": _cmd_decompose,
        "eval": _cmd_eval,
        "shard": _cmd_shard,
        "merge": _cmd_merge,
        "report": _cmd_report,
    }[args.command](args)


if __name__ == "__main__":
    main(sys.argv[1:])
