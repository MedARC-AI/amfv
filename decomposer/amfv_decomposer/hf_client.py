"""HuggingFace inference client for PEFT/LoRA models (transformers + bitsandbytes)."""

from __future__ import annotations

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, pipeline
from peft import PeftModel

_pipelines: dict[str, object] = {}

_BNB_CONFIG = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_compute_dtype=torch.float16,
)

# Standard base for SYX/mistral_based_claim_extractor
# (adapter_config.json references an unsloth repo that is no longer accessible)
_BASE_MODEL = "mistralai/Mistral-7B-Instruct-v0.2"


def get_pipeline(adapter_id: str):
    if adapter_id not in _pipelines:
        base = AutoModelForCausalLM.from_pretrained(
            _BASE_MODEL,
            quantization_config=_BNB_CONFIG,
            device_map="auto",
        )
        model = PeftModel.from_pretrained(base, adapter_id)
        tokenizer = AutoTokenizer.from_pretrained(_BASE_MODEL)
        tokenizer.pad_token_id = tokenizer.eos_token_id
        _pipelines[adapter_id] = pipeline(
            "text-generation",
            model=model,
            tokenizer=tokenizer,
            max_new_tokens=512,
            temperature=None,
            do_sample=False,
        )
    return _pipelines[adapter_id]


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
