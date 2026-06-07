"""VeriScore original: fine-tuned Mistral-7B (SYX/mistral_based_claim_extractor).

Uses the Alpaca prompt format the model was fine-tuned on, with VeriScore's
sliding-window context strategy (3 sentences before, 1 after).
"""

from __future__ import annotations

from ..base import BaseDecomposer, split_sentences, sliding_window, parse_claims
from ..vllm_client import VERISCORE_MISTRAL, completion_generate

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
    """VeriScore with its original fine-tuned Mistral-7B backbone."""

    def decompose(self, text: str, context: str = "") -> list[str]:
        sentences = split_sentences(text)
        if not sentences:
            return []

        prompts = []
        for i in range(len(sentences)):
            snippet, plain_sent = sliding_window(sentences, i)
            prompts.append(
                _ALPACA_TEMPLATE.format(
                    instruction=_INSTRUCTION,
                    snippet=snippet,
                    sentence=plain_sent,
                )
            )

        outputs = completion_generate(prompts, model=VERISCORE_MISTRAL)

        all_claims: list[str] = []
        for output in outputs:
            all_claims.extend(parse_claims(output))
        return all_claims
