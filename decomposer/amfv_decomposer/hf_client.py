"""HuggingFace inference client for PEFT/LoRA models (transformers + bitsandbytes)."""

from __future__ import annotations

import torch
from transformers import AutoTokenizer, pipeline
from peft import AutoPeftModelForCausalLM

_pipelines: dict[str, object] = {}


def get_pipeline(model_id: str):
    if model_id not in _pipelines:
        model = AutoPeftModelForCausalLM.from_pretrained(
            model_id,
            load_in_4bit=True,
            torch_dtype=torch.float16,
            device_map="auto",
        )
        tokenizer = AutoTokenizer.from_pretrained(model_id)
        _pipelines[model_id] = pipeline(
            "text-generation",
            model=model,
            tokenizer=tokenizer,
            max_new_tokens=512,
            temperature=None,
            do_sample=False,
        )
    return _pipelines[model_id]


def hf_generate(prompts: list[str], model_id: str) -> list[str]:
    """Run completion prompts through a PEFT model. Returns only the generated text."""
    pipe = get_pipeline(model_id)
    outputs = pipe(prompts, batch_size=8)
    results = []
    for prompt, output in zip(prompts, outputs):
        generated = output[0]["generated_text"]
        # Strip the input prompt — pipeline returns prompt + generation
        results.append(generated[len(prompt):].strip())
    return results
