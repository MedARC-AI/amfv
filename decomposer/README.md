# AMFV Decomposer

Breaks long-form text — answers, documents, model outputs, reasoning traces —
into atomic, independently verifiable medical claims for the
[verifier](../verifier/README.md), part of the
[Agentic Medical Fact Verifier](../README.md).

Workspace member (`amfv-decomposer`). Training experiments live in
[`../training`](../training/README.md).

## Structure

```
amfv_decomposer/
  base.py                  BaseDecomposer interface + sentence splitting + sliding window
  vllm_client.py           Shared Qwen3-8B vLLM singleton
  baselines/
    factscore.py           FActScore (Min et al. 2023) — sentence-by-sentence, no context
    medscore.py            MedScore (Huang et al. 2025) — medical few-shot + full context
    veriscore.py           VeriScore (Song et al. 2024) — sliding window, conservative filter
evaluate.py                CLI evaluation script
```

## Model note

All decomposers use **Qwen3-8B** (thinking disabled) via vLLM. The original MedScore
paper used GPT-4o-mini and VeriScore used a fine-tuned Mistral-7B. Numbers here are
not directly comparable — this setup enables a fair **prompt-vs-prompt** comparison
on the same backbone.

## Setup

```bash
pip install -e .
python -m spacy download en_core_web_sm
```

## Evaluation

```bash
# Quick sanity check (5 records)
python evaluate.py --data /path/to/AskDocs.demo.jsonl --max-records 5

# Full AskDocsAI run
python evaluate.py --data /path/to/AskDocs.jsonl --output results/

# Single decomposer
python evaluate.py --data /path/to/AskDocs.jsonl --decomposers medscore
```

Outputs a comparison table:

| Method    | Claims/Response | Claims/Sentence | 0-claim rate |
|-----------|----------------|----------------|-------------|
| factscore | ...            | ...            | ...         |
| medscore  | ...            | ...            | ...         |
| veriscore | ...            | ...            | ...         |

Reference numbers from MedScore paper (GPT-4o-mini, AskDocsAI):

| Method    | Claims/Response | Claims/Sentence | 0-claim rate |
|-----------|----------------|----------------|-------------|
| FActScore | 28.60          | 4.24           | 0%          |
| MedScore  | 13.62          | 2.02           | 0%          |
| VeriScore | 3.87           | 0.57           | 14.67%      |
