from __future__ import annotations

import json

import pytest

from amfv_datasets.eval_prompts.decomposition import (
    DecompositionItem,
    active_claims,
    claims_by_type,
    decomposition_user_prompt,
    distractor_claims,
    parse_decomposition_response,
)
from amfv_datasets.eval_prompts.retrieval import (
    RetrievalItem,
    parse_retrieval_response,
    retrieval_user_prompt,
)

SAMPLE_DOC = (
    "Metformin is the first-line pharmacotherapy for type 2 diabetes mellitus in adults "
    "with eGFR ≥ 30 mL/min/1.73 m². "
    "It should be dose-reduced when eGFR falls to 30–44 mL/min/1.73 m² and discontinued "
    "below eGFR 30 mL/min/1.73 m² due to increased risk of lactic acidosis. "
    "Insulin secretagogues such as glipizide may be added if glycaemic targets are not met. "
    "There is no evidence that dual therapy with metformin and a DPP-4 inhibitor reduces "
    "cardiovascular events compared with metformin monotherapy in low-risk patients."
)

GOOD_RETRIEVAL_JSON = json.dumps(
    [
        {
            "question": "What is the minimum eGFR threshold for initiating metformin?",
            "question_type": "verbatim",
            "answer": "eGFR ≥ 30 mL/min/1.73 m²",
            "supporting_spans": ["eGFR ≥ 30 mL/min/1.73 m²"],
            "is_answerable": True,
            "notes": "",
        },
        {
            "question": "When should metformin be discontinued?",
            "question_type": "paragraph",
            "answer": "When eGFR falls below 30 mL/min/1.73 m² due to increased lactic acidosis risk.",
            "supporting_spans": [
                "discontinued below eGFR 30 mL/min/1.73 m² due to increased risk of lactic acidosis"
            ],
            "is_answerable": True,
            "notes": "",
        },
        {
            "question": "What is the cardiovascular event rate reduction from adding sitagliptin to metformin?",
            "question_type": "adversarial",
            "answer": "",
            "supporting_spans": [],
            "is_answerable": False,
            "notes": "Document mentions DPP-4 inhibitors but gives no specific event rate figure.",
        },
    ]
)

GOOD_DECOMP_JSON = json.dumps(
    [
        {
            "claim": "Metformin is the first-line pharmacotherapy for type 2 diabetes mellitus in adults with eGFR ≥ 30 mL/min/1.73 m².",
            "claim_type": "factual",
            "source_span": "Metformin is the first-line pharmacotherapy for type 2 diabetes mellitus in adults with eGFR ≥ 30 mL/min/1.73 m².",
            "requires_coreference": False,
            "original_pronoun": None,
            "resolved_referent": None,
            "is_distractor": False,
            "distractor_reason": None,
            "notes": "",
        },
        {
            "claim": "Metformin should be dose-reduced when eGFR falls to 30–44 mL/min/1.73 m².",
            "claim_type": "numeric",
            "source_span": "dose-reduced when eGFR falls to 30–44 mL/min/1.73 m²",
            "requires_coreference": False,
            "original_pronoun": None,
            "resolved_referent": None,
            "is_distractor": False,
            "distractor_reason": None,
            "notes": "",
        },
        {
            "claim": "There is no evidence that dual therapy with metformin and a DPP-4 inhibitor reduces cardiovascular events compared with metformin monotherapy in low-risk patients.",
            "claim_type": "negation",
            "source_span": "There is no evidence that dual therapy with metformin and a DPP-4 inhibitor reduces cardiovascular events",
            "requires_coreference": False,
            "original_pronoun": None,
            "resolved_referent": None,
            "is_distractor": False,
            "distractor_reason": None,
            "notes": "",
        },
        {
            "claim": "Option A states that metformin causes hypoglycaemia as a primary side effect.",
            "claim_type": "factual",
            "source_span": "Option A: metformin causes hypoglycaemia",
            "requires_coreference": False,
            "original_pronoun": None,
            "resolved_referent": None,
            "is_distractor": True,
            "distractor_reason": "recitation of incorrect MC option",
            "notes": "",
        },
    ]
)

class TestRetrievalUserPrompt:
    def test_contains_document(self):
        prompt = retrieval_user_prompt(SAMPLE_DOC)
        assert SAMPLE_DOC in prompt

    def test_default_counts_mentioned(self):
        prompt = retrieval_user_prompt(SAMPLE_DOC)
        for kind in ("verbatim", "paragraph", "multi_paragraph", "adversarial"):
            assert kind in prompt

    def test_title_and_source_included(self):
        prompt = retrieval_user_prompt(SAMPLE_DOC, document_title="Diabetes Guidelines 2024", document_source="https://nice.org.uk/guidance/ng28")
        assert "Diabetes Guidelines 2024" in prompt
        assert "https://nice.org.uk/guidance/ng28" in prompt

    def test_count_override(self):
        prompt = retrieval_user_prompt(SAMPLE_DOC, counts={"verbatim": 5, "adversarial": 0})
        assert "5 verbatim" in prompt


class TestFindTruncatedSpans:

    def test_flags_span_truncated_right_before_bullets(self):
        doc = (
            "Consider relaxing the target HbA1c level on a case-by-case basis if:\n"
            "- they have a reduced life expectancy\n"
            "- intensive management would not be appropriate.\n"
        )
        truncated = "Consider relaxing the target HbA1c level on a case-by-case basis if:\n"
        item = RetrievalItem(
            question="q", question_type="paragraph", answer="a",
            supporting_spans=[truncated], is_answerable=True,
        )
        assert item.find_truncated_spans(doc) == [truncated]

    def test_does_not_flag_complete_span_including_bullets(self):
        doc = (
            "Consider relaxing the target HbA1c level on a case-by-case basis if:\n"
            "- they have a reduced life expectancy\n"
            "- intensive management would not be appropriate.\n"
        )
        complete = doc.strip()
        item = RetrievalItem(
            question="q", question_type="paragraph", answer="a",
            supporting_spans=[complete], is_answerable=True,
        )
        assert item.find_truncated_spans(doc) == []

    def test_does_not_flag_span_with_no_following_bullets(self):
        doc = "Metformin is first-line therapy. It is well tolerated."
        span = "Metformin is first-line therapy."
        item = RetrievalItem(
            question="q", question_type="verbatim", answer="a",
            supporting_spans=[span], is_answerable=True,
        )
        assert item.find_truncated_spans(doc) == []

    def test_ignores_spans_not_found_in_document(self):
        doc = "Some unrelated document text."
        item = RetrievalItem(
            question="q", question_type="paragraph", answer="a",
            supporting_spans=["This text does not appear anywhere."],
            is_answerable=True,
        )
        assert item.find_truncated_spans(doc) == []

    def test_handles_multiple_spans_mixed(self):
        doc = (
            "Offer isCGM if any of the following apply:\n"
            "- recurrent hypoglycaemia\n"
            "- impaired awareness\n"
        )
        good = "Offer isCGM if any of the following apply:\n- recurrent hypoglycaemia\n- impaired awareness"
        bad = "Offer isCGM if any of the following apply:\n"
        item = RetrievalItem(
            question="q", question_type="paragraph", answer="a",
            supporting_spans=[good, bad], is_answerable=True,
        )
        flagged = item.find_truncated_spans(doc)
        assert bad in flagged
        assert good not in flagged

    def test_handles_crlf_line_endings(self):
        doc = (
            "Consider relaxing the target if:\r\n"
            "- they have a reduced life expectancy\r\n"
            "- intensive management would not be appropriate.\r\n"
        )
        truncated = "Consider relaxing the target if:\r\n- they have a reduced life expectancy\r\n"
        item = RetrievalItem(
            question="q", question_type="paragraph", answer="a",
            supporting_spans=[truncated], is_answerable=True,
        )
        assert item.find_truncated_spans(doc) == [truncated]

    def test_does_not_flag_literal_hyphen_in_prose(self):
        doc = "The treatment was well-tolerated by most patients in the trial."
        span = "The treatment was well"
        item = RetrievalItem(
            question="q", question_type="verbatim", answer="a",
            supporting_spans=[span], is_answerable=True,
        )
        assert item.find_truncated_spans(doc) == []


class TestRepairTruncatedSpans:

    def test_repairs_real_world_failure_case(self):
        doc = (
            "Consider relaxing the target HbA1c level on a case-by-case basis, "
            "with particular consideration for people who are older or frailer, if:\n"
            "- they are unlikely to achieve longer-term risk-reduction benefits, "
            "for example, people with a reduced life expectancy\n"
            "- tight blood glucose control would put them at high risk if they "
            "developed hypoglycaemia\n"
            "- intensive management would not be appropriate, for example if "
            "they have significant comorbidities.\n"
        )
        truncated = (
            "Consider relaxing the target HbA1c level on a case-by-case basis, "
            "with particular consideration for people who are older or frailer, if:\n"
            "- they are unlikely to achieve longer-term risk-reduction benefits, "
            "for example, people with a reduced life expectancy\n"
        )
        item = RetrievalItem(
            question="q", question_type="paragraph", answer="a",
            supporting_spans=[truncated], is_answerable=True,
        )
        assert item.find_truncated_spans(doc) == [truncated]

        n = item.repair_truncated_spans(doc)

        assert n == 1
        assert item.find_truncated_spans(doc) == []
        assert item.validate_spans(doc) == []
        assert "significant comorbidities" in item.supporting_spans[0]
        assert "developed hypoglycaemia" in item.supporting_spans[0]

    def test_repairs_with_crlf_line_endings(self):
        doc = (
            "Consider relaxing the target if:\r\n"
            "- they have a reduced life expectancy\r\n"
            "- intensive management would not be appropriate.\r\n"
        )
        truncated = "Consider relaxing the target if:\r\n- they have a reduced life expectancy\r\n"
        item = RetrievalItem(
            question="q", question_type="paragraph", answer="a",
            supporting_spans=[truncated], is_answerable=True,
        )
        n = item.repair_truncated_spans(doc)
        assert n == 1
        assert item.find_truncated_spans(doc) == []
        assert "intensive management would not be appropriate" in item.supporting_spans[0]

    def test_does_not_repair_when_no_bullets_follow(self):
        doc = "Metformin is first-line therapy. It is well tolerated."
        span = "Metformin is first-line therapy."
        item = RetrievalItem(
            question="q", question_type="verbatim", answer="a",
            supporting_spans=[span], is_answerable=True,
        )
        n = item.repair_truncated_spans(doc)
        assert n == 0
        assert item.supporting_spans == [span]

    def test_does_not_repair_literal_hyphen_in_prose(self):
        doc = "The treatment was well-tolerated by most patients in the trial."
        span = "The treatment was well"
        item = RetrievalItem(
            question="q", question_type="verbatim", answer="a",
            supporting_spans=[span], is_answerable=True,
        )
        n = item.repair_truncated_spans(doc)
        assert n == 0
        assert item.supporting_spans == [span]

    def test_repairs_span_without_trailing_newline(self):
        doc = "Offer X if any of the following apply:\n- criterion A\n- criterion B\n- criterion C\n"
        span = "Offer X if any of the following apply:"
        item = RetrievalItem(
            question="q", question_type="multi_paragraph", answer="a",
            supporting_spans=[span], is_answerable=True,
        )
        n = item.repair_truncated_spans(doc)
        assert n == 1
        for bullet in ("criterion A", "criterion B", "criterion C"):
            assert bullet in item.supporting_spans[0]

    def test_repairs_only_truncated_spans_in_mixed_list(self):
        doc = "Sentence one is fine.\nOffer X if any of the following apply:\n- criterion A\n- criterion B\n"
        good = "Sentence one is fine."
        bad = "Offer X if any of the following apply:\n"
        item = RetrievalItem(
            question="q", question_type="multi_paragraph", answer="a",
            supporting_spans=[good, bad], is_answerable=True,
        )
        n = item.repair_truncated_spans(doc)
        assert n == 1
        assert item.supporting_spans[0] == good
        assert "criterion B" in item.supporting_spans[1]

    def test_leaves_span_unmodified_when_not_found_in_document(self):
        doc = "Completely unrelated text."
        span = "This text does not appear anywhere in the document."
        item = RetrievalItem(
            question="q", question_type="paragraph", answer="a",
            supporting_spans=[span], is_answerable=True,
        )
        n = item.repair_truncated_spans(doc)
        assert n == 0
        assert item.supporting_spans == [span]


class TestParseRetrievalResponse:
    def test_parse_bare_json(self):
        items = parse_retrieval_response(GOOD_RETRIEVAL_JSON)
        assert len(items) == 3
        assert all(isinstance(i, RetrievalItem) for i in items)

    def test_parse_fenced_json(self):
        fenced = f"```json\n{GOOD_RETRIEVAL_JSON}\n```"
        items = parse_retrieval_response(fenced)
        assert len(items) == 3

    def test_adversarial_item_is_not_answerable(self):
        items = parse_retrieval_response(GOOD_RETRIEVAL_JSON)
        adversarial = [i for i in items if i.question_type == "adversarial"]
        assert len(adversarial) == 1
        assert not adversarial[0].is_answerable
        assert adversarial[0].supporting_spans == []
        assert adversarial[0].answer == ""

    def test_verbatim_item_has_span(self):
        items = parse_retrieval_response(GOOD_RETRIEVAL_JSON)
        verbatim = [i for i in items if i.question_type == "verbatim"]
        assert verbatim[0].supporting_spans != []

    def test_span_validation(self):
        items = parse_retrieval_response(GOOD_RETRIEVAL_JSON)
        verbatim = [i for i in items if i.question_type == "verbatim"][0]
        bad = verbatim.validate_spans("completely unrelated text")
        assert len(bad) == len(verbatim.supporting_spans)

        good = verbatim.validate_spans(SAMPLE_DOC)
        assert good == []

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError, match="not valid JSON"):
            parse_retrieval_response("not json at all {{{")

    def test_non_array_raises(self):
        with pytest.raises(ValueError, match="JSON array"):
            parse_retrieval_response('{"question": "What?"}')

    def test_missing_required_field_raises(self):
        bad = json.dumps([{"question_type": "verbatim"}]) 
        with pytest.raises(ValueError, match="missing required field"):
            parse_retrieval_response(bad)


class TestDecompositionUserPrompt:
    def test_contains_text(self):
        prompt = decomposition_user_prompt(SAMPLE_DOC)
        assert SAMPLE_DOC in prompt

    def test_default_source_kind(self):
        prompt = decomposition_user_prompt(SAMPLE_DOC)
        assert "model output" in prompt

    def test_reasoning_trace_kind(self):
        prompt = decomposition_user_prompt(SAMPLE_DOC, source_kind="reasoning_trace")
        assert "reasoning trace" in prompt

    def test_mc_distractor_reminder_shown(self):
        prompt = decomposition_user_prompt(SAMPLE_DOC, source_kind="multiple_choice_rationale")
        assert "Rule 2" in prompt

    def test_mc_distractor_reminder_absent_for_other_kinds(self):
        for kind in ("model_output", "reasoning_trace", "document_passage"):
            prompt = decomposition_user_prompt(SAMPLE_DOC, source_kind=kind)
            assert "Rule 2" not in prompt

    def test_title_and_source_included(self):
        prompt = decomposition_user_prompt(
            SAMPLE_DOC,
            document_title="NICE NG28",
            document_source="https://nice.org.uk",
        )
        assert "NICE NG28" in prompt
        assert "https://nice.org.uk" in prompt

    def test_extra_context_included(self):
        prompt = decomposition_user_prompt(SAMPLE_DOC, extra_context="Focus on renal dosing.")
        assert "Focus on renal dosing." in prompt


class TestParseDecompositionResponse:
    def test_parse_bare_json(self):
        items = parse_decomposition_response(GOOD_DECOMP_JSON)
        assert len(items) == 4

    def test_parse_fenced_json(self):
        fenced = f"```json\n{GOOD_DECOMP_JSON}\n```"
        items = parse_decomposition_response(fenced)
        assert len(items) == 4

    def test_all_items_are_dataclass(self):
        items = parse_decomposition_response(GOOD_DECOMP_JSON)
        assert all(isinstance(i, DecompositionItem) for i in items)

    def test_distractor_flagged(self):
        items = parse_decomposition_response(GOOD_DECOMP_JSON)
        distractors = distractor_claims(items)
        assert len(distractors) == 1
        assert distractors[0].distractor_reason == "recitation of incorrect MC option"

    def test_active_claims_excludes_distractors(self):
        items = parse_decomposition_response(GOOD_DECOMP_JSON)
        active = active_claims(items)
        assert len(active) == 3
        assert all(not c.is_distractor for c in active)

    def test_filter_by_claim_type(self):
        items = parse_decomposition_response(GOOD_DECOMP_JSON)
        numeric = claims_by_type(items, "numeric")
        assert len(numeric) == 1
        assert "30–44" in numeric[0].claim

        negations = claims_by_type(items, "negation")
        assert len(negations) == 1

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError, match="not valid JSON"):
            parse_decomposition_response("}{bad json")

    def test_missing_required_field_raises(self):
        bad = json.dumps([{"claim_type": "factual", "source_span": "x"}])
        with pytest.raises(ValueError, match="missing required field"):
            parse_decomposition_response(bad)