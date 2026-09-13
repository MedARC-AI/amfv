"""Metric computation and reporting for eval results."""

from __future__ import annotations

from collections import defaultdict

__all__ = ["compute_metrics", "print_report"]


def compute_metrics(records: list[dict]) -> dict:
    """Compute aggregate metrics from a list of scored eval records.

    Each record must have at minimum ``"operators"``, ``"n_operators"``, and
    ``"evaluations"`` fields.  Metrics are derived from the *latest* evaluation
    entry in each record's ``"evaluations"`` list.

    Args:
        records: Scored eval records, each containing at least one evaluation.

    Returns:
        Nested dict with keys ``total``, ``passed``, ``pass_rate``,
        ``atom_recall``, ``by_operator``, and ``by_n_operators``.
    """
    print(f"Computing metrics for {len(records)} records…", flush=True)
    print("Records: ", records if records else "<none>", flush=True)
    if not records:
        return {}

    total = len(records)
    n_passed = sum(1 for r in records if r.get("passed", False))
    n_errors = 0

    atom_recalls: list[float] = []
    ctx_precisions: list[float] = []
    eval_scores: list[float] = []
    reward_scores: list[float] = []
    op_noticed: dict[str, list[bool]] = defaultdict(list)
    by_n_op: dict[int, dict] = defaultdict(lambda: {"total": 0, "passed": 0, "errors": 0})

    for r in records:
        evals = r.get("evaluations", [])
        if not evals:
            print(f"Warning: record {r.get('id', '<unknown>')} has no evaluations; skipping.")
            continue
        ev = evals[-1]

        if ev.get("judge_errors"):
            n_errors += 1

        atom_covered: list[bool] | None = ev.get("atom_covered")
        if atom_covered:
            atom_recalls.append(sum(atom_covered) / len(atom_covered))

        ctx_extracted: list[bool] | None = ev.get("context_extracted")
        if ctx_extracted:
            ctx_precisions.append(1.0 - sum(ctx_extracted) / len(ctx_extracted))

        if "eval_score" in ev:
            eval_scores.append(ev["eval_score"])
        if "reward_score" in ev:
            reward_scores.append(ev["reward_score"])

        for op, val in ev.get("operators_noticed", {}).items():
            op_noticed[op].append(bool(val))

        n_ops = r.get("n_operators", 0)
        by_n_op[n_ops]["total"] += 1
        by_n_op[n_ops]["passed"] += int(r.get("passed", False))
        by_n_op[n_ops]["errors"] += int(bool(ev.get("judge_errors")))

    return {
        "total": total,
        "passed": n_passed,
        "errors": n_errors,
        "pass_rate": n_passed / total if total else 0.0,
        "atom_recall": _mean(atom_recalls),
        "context_precision": _mean(ctx_precisions) if ctx_precisions else None,
        "eval_score": _mean(eval_scores) if eval_scores else None,
        "reward_score": _mean(reward_scores) if reward_scores else None,
        "by_operator": {
            op: _mean([float(v) for v in vals])
            for op, vals in sorted(op_noticed.items())
        },
        "by_n_operators": {
            n: {
                "total": d["total"],
                "passed": d["passed"],
                "errors": d["errors"],
                "pass_rate": d["passed"] / d["total"] if d["total"] else 0.0,
            }
            for n, d in sorted(by_n_op.items())
        },
    }


def print_report(metrics: dict, *, title: str = "Results") -> None:
    """Print a human-readable metrics report to stdout.

    Args:
        metrics: Output of :func:`compute_metrics`.
        title: Header line for the report.
    """
    if not metrics:
        print("No metrics to report.")
        return

    w = 56
    print(f"\n{'─' * w}")
    print(f"  {title}")
    print(f"{'─' * w}")

    total = metrics["total"]
    passed = metrics["passed"]
    errors = metrics.get("errors", 0)
    print(f"\n  Pass rate        {passed:,} / {total:,}  ({metrics['pass_rate']:.1%})")
    if errors:
        print(f"  Judge errors     {errors:,} / {total:,}  ({errors / total:.1%})")
    print(f"  Atom recall      {metrics['atom_recall']:.3f}")
    ctx_prec = metrics.get("context_precision")
    if ctx_prec is not None:
        print(f"  Context precision {ctx_prec:.3f}")
    eval_score = metrics.get("eval_score")
    if eval_score is not None:
        print(f"  Eval score       {eval_score:.3f}")
    reward_score = metrics.get("reward_score")
    if reward_score is not None:
        print(f"  Reward score     {reward_score:.3f}")

    by_op = metrics.get("by_operator", {})
    if by_op:
        overall_op = _mean(list(by_op.values()))
        print(f"\n  Operator awareness  {overall_op:.3f} overall")
        for op, rate in by_op.items():
            print(f"    {op:<28}  {rate:.3f}")

    by_n = metrics.get("by_n_operators", {})
    if by_n:
        print("\n  By operator count")
        for n, d in by_n.items():
            label = f"  {n} op{'s' if n != 1 else ' '}"
            bar = f"{d['passed']:,} / {d['total']:,}  ({d['pass_rate']:.1%})"
            err_note = f"  [{d['errors']} errors]" if d.get("errors") else ""
            print(f"    {label:<8}  {bar}{err_note}")

    print()


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0
