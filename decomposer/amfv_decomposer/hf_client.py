"""HuggingFace inference client for PEFT/LoRA models (transformers + bitsandbytes).

Requires the [hf] optional dependencies: pip install -e ".[hf]"
Imports are lazy so this module can be imported without peft/bitsandbytes installed.
"""

from __future__ import annotations

from typing import Any

_pipelines: dict[str, Any] = {}

# SYX/mistral_based_claim_extractor was fine-tuned on mistralai/Mistral-7B-v0.1
# (base, not instruct), but that adapter's config references an unsloth repo
# that is no longer accessible, so we fall back to Instruct-v0.2.
# The LoRA delta is applied to a different weight matrix than intended, which
# may silently degrade claim extraction quality for veriscore_original.
_BASE_MODEL = "mistralai/Mistral-7B-Instruct-v0.2"


def get_pipeline(adapter_id: str) -> Any:
    if adapter_id not in _pipelines:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, pipeline
        from peft import PeftModel  # type: ignore[import-not-found]

        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.float16,
        )
        base = AutoModelForCausalLM.from_pretrained(
            _BASE_MODEL,
            quantization_config=bnb_config,
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
            return_full_text=False,
        )
    return _pipelines[adapter_id]


def hf_generate(prompts: list[str], model_id: str) -> list[str]:
    """Run completion prompts through a PEFT model. Returns only the generated text."""
    pipe = get_pipeline(model_id)
    outputs: list[list[dict[str, str]]] = pipe(prompts, batch_size=8)
    return [output[0]["generated_text"].strip() for output in outputs]
