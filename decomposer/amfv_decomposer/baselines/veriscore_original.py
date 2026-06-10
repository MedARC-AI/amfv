"""VeriScore original: fine-tuned Mistral-7B claim extractor (SYX/mistral_based_claim_extractor).

Replicates the original fine-tuned mode of github.com/Yixiao-Song/VeriScore
(claim_extractor.py, `if self.model` branch): the *entire* response — prefixed
with the question in QA mode — is fed through the Alpaca template with the
target sentence marked inline with <SOS>/<EOS>. No sliding window; the
window-based snippets belong to the prompting mode only (see veriscore.py).
"""

from __future__ import annotations

from ..base import BaseDecomposer, parse_claims
from .veriscore import NO_CLAIM_SENTINEL

MODEL_ID = "SYX/mistral_based_claim_extractor"

# Verbatim from prompt/extraction_alpaca_template.txt; formatted positionally
# as alpaca_prompt.format(snippet, ""), exactly like the original.
_ALPACA_TEMPLATE = """Below is an instruction that describes a task, paired with an input that provides further context. Write a response that appropriately completes the request.

### Instruction:
You are trying to verify how factual a piece of text is. To do so, you need to break down a sentence and extract as many fine-grained facts mentioned in the sentence as possible. Each of these fine-grained facts should be verifiable against reliable external world knowledge (e.g., via Wikipedia). Any story, personal experiences, hypotheticals (e.g., "would be" or subjunctive), subjective statements (e.g., opinions), suggestions, advice, instructions, and other such content should not be included in the list. Biographical, historical, scientific, and other such texts are not personal experiences or stories. You should extract verifiable facts from them. Each fact should also be describing either one single event (e.g., "Nvidia is founded in 1993 in Sunnyvale, California, U.S.") or single state (e.g., "UMass Amherst has existed for 161 years.") with necessary time and location information. Quotations should be extracted verbatim with the source when available. Listed references should be ignored.

Extract fine-grained facts from the sentence marked between <SOS> and <EOS>. You should focus on the named entities and numbers in the sentence and extract relevant information from the sentence. Other sentences are only context for you to recover pronouns, definite phrases (e.g., "the victims" or "the pope"), and so on. Each fact should be understandable on its own and require no additional context. This means that all entities must be referred to by name but not pronoun. Use the name of entities rather than definite noun phrases (e.g., 'the teacher') whenever possible. If a definite noun phrase is used, be sure to add modifiers (e.g., a embedded clause, a prepositional phrase, etc.). Each fact must be situated within relevant temporal and location whenever needed. Keep each fact to one sentence with zero or at most one embedded clause.

If there is no verifiable fact in the sentence, please write "No verifiable claim."

### Input:
{}

### Response:
{}"""


class VeriScoreOriginalDecomposer(BaseDecomposer):
    """VeriScore's fine-tuned Mistral-7B extractor (PEFT + 4-bit), original input format."""

    backend = "hf"
    model_id = MODEL_ID
    default_context_key = "question"
    dedup_record = True  # each sentence re-reads the whole response; repeats are common

    def build_requests(self, text: str, sentences: list[str], context: str) -> list:
        r"""One Alpaca prompt per sentence over the full response, as upstream.

        qa_scanner_extractor builds "Questions:\n{q}\nResponse:\n{response}"
        (sic, plural) and tags the target via str.replace — which, as upstream,
        tags every occurrence of a repeated sentence.
        """
        if context:
            source = f"Questions:\n{context.strip()}\nResponse:\n{text.strip()}"
        else:
            source = text.strip()
        return [
            _ALPACA_TEMPLATE.format(source.replace(sent, f"<SOS>{sent}<EOS>"), "")
            for sent in sentences
        ]

    def parse_output(self, raw: str) -> list[str]:
        """Whole-generation sentinel discard, then robust shared parsing.

        Deviation: the original keeps lines verbatim (its fine-tuned model
        emits plain claims); we route through parse_claims to guard against
        chat-style drift from the Instruct base under the LoRA.
        """
        out = raw.replace("</s>", "").strip()
        if not out or NO_CLAIM_SENTINEL in out:
            return []
        return parse_claims(out)
