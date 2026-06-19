from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Literal

QuestionType = Literal["verbatim", "paragraph", "multi_paragraph", "adversarial"]


@dataclass
class RetrievalItem:
    question: str
    question_type: QuestionType
    answer: str
    supporting_spans: list[str] = field(default_factory=list)
    is_answerable: bool = True
    notes: str = ""

    _BULLET_LINE_RE = re.compile(r"[\r\n]*- [^\r\n]*")

    def validate_spans(self, document: str) -> list[str]:
        return [s for s in self.supporting_spans if s not in document]

    @staticmethod
    def _ends_at_bullet_boundary(span: str, following: str) -> bool:
        if span.endswith(("\n", "\r")):
            return following.startswith("- ")
        return bool(re.match(r"[\r\n]+- ", following))

    def find_truncated_spans(self, document: str) -> list[str]:
        flagged = []
        for span in self.supporting_spans:
            idx = document.find(span)
            if idx == -1:
                continue
            end = idx + len(span)
            following = document[end:end + 8]
            if self._ends_at_bullet_boundary(span, following):
                flagged.append(span)
        return flagged

    def repair_truncated_spans(self, document: str) -> int:
        repaired_count = 0
        new_spans = []
        for span in self.supporting_spans:
            idx = document.find(span)
            if idx == -1:
                new_spans.append(span)
                continue

            cursor = idx + len(span)
            extended = False
            first_iteration = True

            while True:
                following = document[cursor:cursor + 4]
                if first_iteration:
                    boundary_ok = self._ends_at_bullet_boundary(span, following)
                else:
                    boundary_ok = following.startswith(("\n", "\r"))
                if not boundary_ok:
                    break

                m = self._BULLET_LINE_RE.match(document, cursor)
                if not m or m.end() == cursor:
                    break
                cursor = m.end()
                extended = True
                first_iteration = False

            if extended:
                new_spans.append(document[idx:cursor])
                repaired_count += 1
            else:
                new_spans.append(span)

        self.supporting_spans = new_spans
        return repaired_count


SYSTEM_PROMPT = """\
You are an expert medical information retrieval evaluator helping build an \
evaluation dataset for a medical fact verification system.

Your task is to generate question-and-answer pairs from a provided medical \
document or passage. Each question targets one of four difficulty categories:

CATEGORY DEFINITIONS
====================
1. verbatim
   The answer appears word-for-word in the text. A retrieval system that \
returns the right passage trivially has the answer. Include at least one \
highly specific clinical detail (dosage, lab value, drug name, procedure \
code, etc.) so the question cannot be answered from general knowledge alone.

2. paragraph
   The answer requires synthesising information from a SINGLE paragraph—it \
cannot be lifted verbatim but does not require crossing paragraph boundaries. \
Questions should involve reasoning such as inferring a clinical implication, \
combining two sentences, or paraphrasing a recommendation with altered \
framing.

3. multi_paragraph
   The answer requires combining information from TWO OR MORE discontinuous \
passages in the document. The passages should not be adjacent. This tests \
whether a retrieval system can find and assemble non-contiguous evidence.

4. adversarial
   The question LOOKS like it should be answerable from the document based on \
its topic and terminology, but the specific answer is NOT present. The ideal \
adversarial question is one where a hallucinating model might confidently give \
a plausible-sounding wrong answer. Leave ``answer`` as an empty string and \
``supporting_spans`` as an empty array for these items.

OUTPUT FORMAT
=============
Respond with a JSON array and nothing else—no preamble, no markdown fences, \
no trailing commentary.  Each element must be an object with these keys:

  question        (string)  The question text.
  question_type   (string)  One of: verbatim, paragraph, multi_paragraph, adversarial
  answer          (string)  Gold answer; empty string for adversarial items.
  supporting_spans (array)  Exact substrings (copy-pasted) from the document
                            that support the answer; empty array for adversarial.
  is_answerable   (boolean) false only for adversarial items.
  notes           (string)  Optional annotation; use "" if none.

QUALITY RULES
=============
- Every answerable question must be answerable ONLY from the provided document, \
not from general medical knowledge.
- supporting_spans must be verbatim substrings of the source—do not paraphrase.
- Do not create questions whose answers span the entire document; evidence spans \
should be locatable passages.
- Adversarial questions must concern a topic the document discusses but must \
ask for a specific detail the document omits (e.g., a dosage range for a drug \
mentioned only by name, a contraindication not listed, a guideline not cited).
- Vary clinical domains across items where the source permits (dosing, diagnosis \
criteria, population restrictions, procedure steps, prognosis, etc.).
- Aim for clinical specificity: prefer "What is the recommended dose of \
metformin for adults with eGFR 30–44?" over "What drug is recommended?".
- When a sentence introduces a bulleted/numbered list (e.g. ends in ":" or \
"if:" or "any of the following"), and the list items are needed to answer the \
question, the supporting_span MUST include the full list, not just the \
introductory sentence. A span that stops right before the bullets it is \
introducing is incomplete and unusable as evidence—copy the entire passage \
including every relevant bullet line, exactly as it appears (including the \
"- " prefix and line breaks).
"""

_DEFAULT_COUNTS: dict[QuestionType, int] = {
    "verbatim": 2,
    "paragraph": 2,
    "multi_paragraph": 2,
    "adversarial": 2,
}


def user_prompt(
    document: str,
    *,
    counts: dict[QuestionType, int] | None = None,
    document_title: str = "",
    document_source: str = "",
) -> str:
    counts = {**_DEFAULT_COUNTS, **(counts or {})}

    header_parts = []
    if document_title:
        header_parts.append(f"Title: {document_title}")
    if document_source:
        header_parts.append(f"Source: {document_source}")
    header = "\n".join(header_parts)

    count_instructions = "\n".join(
        f"  - {n} {qt} item{'s' if n != 1 else ''}"
        for qt, n in counts.items()
    )

    return f"""\
Generate retrieval evaluation items from the medical document below.

{header + chr(10) if header else ""}\
Produce exactly:
{count_instructions}

DOCUMENT
========
{document}
"""


_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def parse_response(raw: str) -> list[RetrievalItem]:
    text = raw.strip()
    match = _JSON_BLOCK_RE.search(text)
    if match:
        text = match.group(1)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Response is not valid JSON: {exc}\n\nRaw:\n{raw[:500]}") from exc

    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON array at the top level, got {type(data).__name__}")

    items: list[RetrievalItem] = []
    for i, obj in enumerate(data):
        try:
            items.append(
                RetrievalItem(
                    question=obj["question"],
                    question_type=obj["question_type"],
                    answer=obj.get("answer", ""),
                    supporting_spans=obj.get("supporting_spans", []),
                    is_answerable=obj.get("is_answerable", True),
                    notes=obj.get("notes", ""),
                )
            )
        except (KeyError, TypeError) as exc:
            raise ValueError(f"Item {i} is missing required field: {exc}\n\nItem: {obj}") from exc

    return items


retrieval_user_prompt = user_prompt
parse_retrieval_response = parse_response