"""VeriScore baseline: sliding-window claim extraction with conservative verifiability filter.

Original uses a fine-tuned Mistral-7B (SYX/mistral_based_claim_extractor). Here we
use Qwen3-8B with the same system prompt and input format for a fair prompt comparison.
"""

from __future__ import annotations

from ..base import BaseDecomposer, split_sentences, sliding_window, parse_claims
from ..vllm_client import QWEN3_8B, chat_generate

# System prompt from VeriScore (Song et al. 2024, github.com/Yixiao-Song/VeriScore)
_SYSTEM = (
    "You are a helpful assistant who can extract verifiable atomic claims from a piece of text. "
    "Each atomic claim should be verifiable against reliable external world knowledge (e.g., via Wikipedia). "
    "Extract only claims from the sentence marked with <SOS> and <EOS> tags. "
    "Each claim must: describe a single event or state; use entity names instead of pronouns; "
    "be self-contained and require no additional context to verify. "
    "Exclude personal experiences, hypothetical scenarios, subjective opinions, suggestions, and advice. "
    'Output one claim per line prefixed with "- ". '
    'If the target sentence contains no verifiable claims, write "No verifiable claim."'
)


class VeriScoreDecomposer(BaseDecomposer):
    """Sliding-window decomposer: 3 sentences before + target + 1 sentence after."""

    def decompose(self, text: str, context: str = "") -> list[str]:
        sentences = split_sentences(text)
        if not sentences:
            return []

        messages_batch = []
        for i in range(len(sentences)):
            snippet, plain_sent = sliding_window(sentences, i)
            user_content = (
                f"Text: {snippet}\n"
                f"Sentence to be focused on: {plain_sent}\n"
                f"Facts:"
            )
            messages_batch.append([
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user_content},
            ])

        outputs = chat_generate(messages_batch, model=QWEN3_8B)

        all_claims: list[str] = []
        for output in outputs:
            all_claims.extend(parse_claims(output))
        return all_claims
