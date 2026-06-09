"""VeriScore baseline: sliding-window claim extraction with conservative verifiability filter.

Original uses a fine-tuned Mistral-7B (SYX/mistral_based_claim_extractor). Here we
use Qwen3-8B with the same system prompt and input format for a fair prompt comparison.
"""

from __future__ import annotations

from ..base import BaseDecomposer, sliding_window

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

    default_context_key = "question"  # prepended to sliding window for QA tasks

    def build_requests(self, text: str, sentences: list[str], context: str) -> list:
        """One system+user message pair per sentence, with sliding-window context."""
        requests = []
        for i in range(len(sentences)):
            snippet, plain_sent = sliding_window(sentences, i)
            # VeriScore (Song et al. 2024): "For QA tasks, we always prepend
            # the question to the sliding window."
            if context:
                snippet = f"{context} {snippet}"
            user_content = (
                f"Text: {snippet}\n"
                f"Sentence to be focused on: {plain_sent}\n"
                f"Facts:"
            )
            requests.append([
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user_content},
            ])
        return requests
