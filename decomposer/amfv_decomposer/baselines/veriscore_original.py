"""VeriScore original: fine-tuned Mistral-7B (SYX/mistral_based_claim_extractor).

Uses transformers + peft + bitsandbytes to load the exact model used in the
original VeriScore paper (4-bit quantized base + LoRA adapter), with VeriScore's
sliding-window context strategy (3 sentences before, 1 after).
"""

from __future__ import annotations

from ..base import BaseDecomposer, sliding_window

MODEL_ID = "SYX/mistral_based_claim_extractor"

_INSTRUCTION = (
    "You are given a piece of text and one target sentence from that text marked with "
    "<SOS> and <EOS> tags. Extract all fine-grained verifiable atomic claims from the "
    "target sentence. Each claim must be verifiable against reliable external world "
    "knowledge (e.g., via Wikipedia). Use entity names instead of pronouns. "
    "Output one claim per line prefixed with '- '. "
    "If there are no verifiable claims, write 'No verifiable claim.'"
)

_ALPACA_TEMPLATE = (
    "Below is an instruction that describes a task, paired with an input that provides "
    "further context. Write a response that appropriately completes the request.\n\n"
    "### Instruction:\n{instruction}\n\n"
    "### Input:\n"
    "Text: {snippet}\n"
    "Sentence to be focused on: {sentence}\n\n"
    "### Response:\n"
)


class VeriScoreOriginalDecomposer(BaseDecomposer):
    """VeriScore with its original fine-tuned Mistral-7B backbone (PEFT + 4-bit)."""

    backend = "hf"
    model_id = MODEL_ID
    default_context_key = "question"  # prepended to sliding window for QA tasks

    def build_requests(self, text: str, sentences: list[str], context: str) -> list:
        """One Alpaca-format prompt string per sentence, with sliding-window context."""
        prompts = []
        for i in range(len(sentences)):
            snippet, plain_sent = sliding_window(sentences, i)
            if context:
                snippet = f"{context} {snippet}"
            prompts.append(
                _ALPACA_TEMPLATE.format(
                    instruction=_INSTRUCTION,
                    snippet=snippet,
                    sentence=plain_sent,
                )
            )
        return prompts
