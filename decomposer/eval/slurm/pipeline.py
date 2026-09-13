#!/usr/bin/env python3
"""SLURM pipeline orchestration for amfv-eval.

Reads a YAML config and submits the full pipeline as dependent SLURM jobs:
  generate → filter[*] → shard → decompose[*] → merge → eval[*] → merge

Re-running this script is safe: completed steps (marked with .done files) are
skipped and only outstanding jobs are submitted.

Usage:
    DIR=/mount/marii python slurm/pipeline.py --config config.yaml [--dry-run]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
from pathlib import Path

import jinja2
import yaml

# Base directory for resolving relative paths from YAML config.
# Set via DIR environment variable: DIR=/mount/marii python slurm/pipeline.py …
_BASE_DIR = Path(os.environ.get("DIR", ".")).resolve()


def _resolve(path_str: str | Path | None) -> Path | None:
    """Resolve a path against DIR if relative; absolute paths are unchanged."""
    if path_str is None:
        return None
    p = Path(path_str)
    return p if p.is_absolute() else _BASE_DIR / p


# ---------------------------------------------------------------------------
# SLURM helpers
# ---------------------------------------------------------------------------


def sbatch(
    template: Path,
    *,
    context: dict,
    dependency: str | None = None,
    array: int | None = None,
    max_concurrent: int | None = None,
    dry_run: bool = False,
) -> str:
    """Render a Jinja2 template, write it to a temp file, and submit with sbatch."""
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(template.parent)),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    rendered = env.get_template(template.name).render(**context)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".sbatch", delete=False, prefix="amfv_"
    ) as f:
        f.write(rendered)
        tmp_path = f.name

    cmd = ["sbatch", "--parsable"]
    if dependency:
        cmd += [f"--dependency=afterok:{dependency}"]
    if array is not None:
        array_spec = f"0-{array - 1}"
        if max_concurrent:
            array_spec += f"%{max_concurrent}"
        cmd += [f"--array={array_spec}"]
    cmd.append(tmp_path)

    if dry_run:
        print(f"[dry-run] {' '.join(cmd)}")
        print(rendered)
        os.unlink(tmp_path)
        return "DRY_RUN"

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        job_id = result.stdout.strip()
        os.unlink(tmp_path)
        print(f"Submitted {template.name} → job {job_id}")
        return job_id
    except subprocess.CalledProcessError as e:
        print(f"sbatch failed for {template.name} (rendered script: {tmp_path}):\n{e.stderr}", flush=True)
        raise


def is_done(marker: Path) -> bool:
    return marker.exists()


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def run_pipeline(config_path: Path, dry_run: bool = False) -> None:
    with config_path.open() as f:
        cfg = yaml.safe_load(f)

    slurm_dir = Path(__file__).parent.resolve()
    markers_dir = _BASE_DIR / ".slurm_markers"
    markers_dir.mkdir(parents=True, exist_ok=True)

    tensor_parallel: int = cfg.get("tensor_parallel", 4)
    n_shards: int = cfg.get("n_shards", 8)
    default_qos: str | None = cfg.get("qos", None)
    max_gpus: int | None = cfg.get("max_gpus", None)
    max_concurrent_shards: int | None = max_gpus // tensor_parallel if max_gpus else None
    output_dir = str(_resolve(cfg["output_dir"]) if "output_dir" in cfg else _BASE_DIR)

    def qos_for(step_cfg: dict) -> str | None:
        return step_cfg.get("qos", default_qos)

    account: str | None = cfg.get("account", None)

    # Context fields shared by all GPU jobs
    gpu_common: dict = {
        "config": str(config_path.resolve()),
        "gpus": tensor_parallel,
        "cpus_per_gpu": cfg.get("cpus_per_gpu", 16),
        "partition": cfg.get("partition", None),
        "account": account,
        "slurm_resume": cfg.get("slurm_resume", False),
        "nice": cfg.get("nice", None),
        "mail_type": cfg.get("mail_type", None),
        "mail_user": cfg.get("mail_user", None),
        "output_dir": output_dir,
        "slurm_dir": str(slurm_dir),
        "hf_home": str(_BASE_DIR / ".cache"),
        "vllm_image": os.environ.get("VLLM_IMAGE", "docker://vllm/vllm-openai:latest"),
        "time_limit": cfg.get("time_limit", None),
    }

    # Context fields shared by CPU-only jobs (shard, merge)
    cpu_common: dict = {
        "qos": default_qos,
        "partition": cfg.get("cpu_partition", cfg.get("partition", None)),
        "account": account,
        "nice": cfg.get("nice", None),
        "mail_type": cfg.get("mail_type", None),
        "mail_user": cfg.get("mail_user", None),
        "output_dir": output_dir,
        "slurm_dir": str(slurm_dir),
    }

    prev_job: str | None = None

    # ------------------------------------------------------------------
    # 0. Embed claims (optional — produces similar_pairs file used by generate)
    # ------------------------------------------------------------------
    embed_cfg = cfg.get("embed_claims")
    if embed_cfg:
        embed_marker = markers_dir / "embed_claims.done"
        if is_done(embed_marker):
            print("embed_claims: already done, skipping.")
        else:
            prev_job = sbatch(
                slurm_dir / "embed_claims.sbatch",
                context={
                    **gpu_common,
                    "job_name": "amfv-embed-claims",
                    "qos": qos_for(embed_cfg),
                    "marker": str(embed_marker),
                },
                dependency=prev_job,
                dry_run=dry_run,
            )

    # ------------------------------------------------------------------
    # 1. Generate
    # ------------------------------------------------------------------
    gen_cfg = cfg["generate"]
    gen_marker = markers_dir / "generate.done"

    if is_done(gen_marker):
        print("generate: already done, skipping.")
    else:
        prev_job = sbatch(
            slurm_dir / "generate.sbatch",
            context={
                **gpu_common,
                "job_name": "amfv-generate",
                "model": gen_cfg["model"],
                "qos": qos_for(gen_cfg),
                "marker": str(gen_marker),
            },
            dependency=prev_job,
            dry_run=dry_run,
        )

    # ------------------------------------------------------------------
    # 2. Filter steps (sequential)
    # ------------------------------------------------------------------
    for i, filt_cfg in enumerate(cfg.get("filter", [])):
        marker = markers_dir / f"filter_{i}.done"
        if is_done(marker):
            print(f"filter[{i}]: already done, skipping.")
            continue
        prev_job = sbatch(
            slurm_dir / "filter.sbatch",
            context={
                **gpu_common,
                "job_name": f"amfv-filter-{i}",
                "model": filt_cfg["model"],
                "qos": qos_for(filt_cfg),
                "step": i,
                "marker": str(marker),
            },
            dependency=prev_job,
            dry_run=dry_run,
        )

    # Determine the dataset output (last filter or generate)
    filter_cfgs = cfg.get("filter", [])
    dataset_path = _resolve(filter_cfgs[-1]["out"] if filter_cfgs else gen_cfg["out"])

    # ------------------------------------------------------------------
    # 3. Shard the dataset once (shared across all decompose models)
    # ------------------------------------------------------------------
    shard_dir = dataset_path.parent / "shards_dataset"
    shard_marker = markers_dir / "shard_dataset.done"

    if is_done(shard_marker):
        print("shard (dataset): already done, skipping.")
        shard_job: str | None = None
    else:
        shard_job = sbatch(
            slurm_dir / "shard.sbatch",
            context={
                **cpu_common,
                "job_name": "amfv-shard-dataset",
                "input": str(dataset_path),
                "n_shards": n_shards,
                "out_dir": str(shard_dir),
                "marker": str(shard_marker),
            },
            dependency=prev_job,
            dry_run=dry_run,
        )

    after_shard = shard_job or prev_job

    # ------------------------------------------------------------------
    # 4. Decompose + Eval (one chain per decompose/eval pair)
    # ------------------------------------------------------------------
    for i, dec_cfg in enumerate(cfg.get("decompose", [])):
        dec_out = _resolve(dec_cfg["out"])
        dec_out_dir = dec_out.parent / f"shards_decompose_{i}"
        dec_marker = markers_dir / f"decompose_{i}.done"
        merge_dec_marker = markers_dir / f"merge_decompose_{i}.done"

        # Decompose array job
        if is_done(dec_marker):
            print(f"decompose[{i}]: already done, skipping.")
            dec_job: str | None = None
        else:
            dec_job = sbatch(
                slurm_dir / "decompose.sbatch",
                context={
                    **gpu_common,
                    "job_name": f"amfv-decompose-{i}",
                    "model": dec_cfg["model"],
                    "qos": qos_for(dec_cfg),
                    "step": i,
                    "shard_dir": str(shard_dir),
                    "out_dir": str(dec_out_dir),
                    "marker": str(dec_marker),
                },
                dependency=after_shard,
                array=n_shards,
                max_concurrent=max_concurrent_shards,
                dry_run=dry_run,
            )

        # Merge decompose shards
        if is_done(merge_dec_marker):
            print(f"merge decompose[{i}]: already done, skipping.")
            merge_dec_job: str | None = None
        else:
            merge_dec_job = sbatch(
                slurm_dir / "merge.sbatch",
                context={
                    **cpu_common,
                    "job_name": f"amfv-merge-decompose-{i}",
                    "in_dir": str(dec_out_dir),
                    "out": str(dec_out),
                    "marker": str(merge_dec_marker),
                },
                dependency=dec_job or after_shard,
                dry_run=dry_run,
            )

        after_merge_dec = merge_dec_job or after_shard

        # Find eval steps that consume this decompose output
        for j, eval_cfg in enumerate(cfg.get("eval", [])):
            if _resolve(eval_cfg.get("input")) != dec_out:
                continue

            eval_out = _resolve(eval_cfg["out"])
            eval_shard_dir = eval_out.parent / f"shards_eval_{i}_{j}"
            eval_out_dir = eval_out.parent / f"shards_eval_out_{i}_{j}"
            eval_shard_marker = markers_dir / f"shard_eval_{i}_{j}.done"
            eval_marker = markers_dir / f"eval_{i}_{j}.done"
            merge_eval_marker = markers_dir / f"merge_eval_{i}_{j}.done"

            # Shard decomposed output for eval
            if is_done(eval_shard_marker):
                print(f"shard eval[{i},{j}]: already done, skipping.")
                eval_shard_job: str | None = None
            else:
                eval_shard_job = sbatch(
                    slurm_dir / "shard.sbatch",
                    context={
                        **cpu_common,
                        "job_name": f"amfv-shard-eval-{i}-{j}",
                        "input": str(dec_out),
                        "n_shards": n_shards,
                        "out_dir": str(eval_shard_dir),
                        "marker": str(eval_shard_marker),
                    },
                    dependency=after_merge_dec,
                    dry_run=dry_run,
                )

            after_eval_shard = eval_shard_job or after_merge_dec

            # Eval array job
            if is_done(eval_marker):
                print(f"eval[{i},{j}]: already done, skipping.")
                eval_job: str | None = None
            else:
                eval_job = sbatch(
                    slurm_dir / "eval.sbatch",
                    context={
                        **gpu_common,
                        "job_name": f"amfv-eval-{j}",
                        "model": eval_cfg["model"],
                        "qos": qos_for(eval_cfg),
                        "step": j,
                        "shard_dir": str(eval_shard_dir),
                        "out_dir": str(eval_out_dir),
                        "marker": str(eval_marker),
                    },
                    dependency=after_eval_shard,
                    array=n_shards,
                    max_concurrent=max_concurrent_shards,
                    dry_run=dry_run,
                )

            # Merge eval shards
            if is_done(merge_eval_marker):
                print(f"merge eval[{i},{j}]: already done, skipping.")
                merge_eval_job: str | None = None
            else:
                merge_eval_job = sbatch(
                    slurm_dir / "merge.sbatch",
                    context={
                        **cpu_common,
                        "job_name": f"amfv-merge-eval-{i}-{j}",
                        "in_dir": str(eval_out_dir),
                        "out": str(eval_out),
                        "marker": str(merge_eval_marker),
                    },
                    dependency=eval_job or after_eval_shard,
                    dry_run=dry_run,
                )

            # Report
            report_marker = markers_dir / f"report_{i}_{j}.done"
            if is_done(report_marker):
                print(f"report[{i},{j}]: already done, skipping.")
            else:
                metrics_out = eval_out.parent / (eval_out.stem + ".metrics.json")  # type: ignore[union-attr]
                sbatch(
                    slurm_dir / "report.sbatch",
                    context={
                        **cpu_common,
                        "job_name": f"amfv-report-{i}-{j}",
                        "input": str(eval_out),
                        "out": str(metrics_out),
                        "marker": str(report_marker),
                    },
                    dependency=merge_eval_job or eval_job or after_eval_shard,
                    dry_run=dry_run,
                )

    print("\nPipeline submitted.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Submit amfv-eval pipeline to SLURM.")
    parser.add_argument("--config", type=Path, required=True, help="Pipeline YAML config.")
    parser.add_argument("--dry-run", action="store_true", help="Print rendered scripts without submitting.")
    args = parser.parse_args()
    run_pipeline(args.config, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
