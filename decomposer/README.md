# AMFV Decomposer

Breaks long-form text into atomic, independently verifiable medical claims.
Part of the [Agentic Medical Fact Verifier](../README.md).

## Setup

```bash
uv sync
```

## Local evaluation

```bash
# Sanity check (5 records)
python evaluate.py --data ../datasets/AskDocs.demo.jsonl --max-records 5

# Full run
python evaluate.py --data ../datasets/AskDocs.jsonl

# With Qwen3 thinking enabled
python evaluate.py --data ../datasets/AskDocs.jsonl --enable-thinking
```

## SLURM evaluation

> Pass all mounts as one comma-separated `--container-mounts` — multiple flags override rather than merge in Pyxis.

```bash
# No-think (default)
sbatch \
    --container-mounts=/data/aymane.ouraq:/data/aymane.ouraq,/data/hf_cache:/data/hf_cache \
    --gpus-per-task=1 \
    decomposer/run_eval.sh \
    --model Qwen/Qwen3-8B --tp 1 \
    --data /admin/home/aymane.ouraq/amfv/datasets/AskDocs.jsonl

# With thinking
sbatch \
    --container-mounts=/data/aymane.ouraq:/data/aymane.ouraq,/data/hf_cache:/data/hf_cache \
    --gpus-per-task=1 \
    decomposer/run_eval.sh \
    --model Qwen/Qwen3-8B --tp 1 \
    --data /admin/home/aymane.ouraq/amfv/datasets/AskDocs.jsonl \
    --enable-thinking

# VeriScore original (Mistral-7B PEFT)
sbatch decomposer/run_eval_mistral.sh
```

`--gpus-per-task` must equal `--tp`. Results land in `results/<model>[-think]/<dataset>/<timestamp>/`.

## Tests

```bash
python3 -m pytest tests/
```

## Reference numbers (MedScore paper, GPT-4o-mini, AskDocsAI)

| Method    | Claims/Response | Claims/Sentence | 0-claim rate |
|-----------|----------------|----------------|-------------|
| FActScore | 28.60          | 4.24           | 0%          |
| MedScore  | 13.62          | 2.02           | 0%          |
| VeriScore | 3.87           | 0.57           | 14.67%      |
