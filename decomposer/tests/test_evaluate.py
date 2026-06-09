"""Tests for evaluate.py and the shared decomposer pipeline.

These tests require no running server; they exercise only pure-Python logic.
Run with: pytest tests/
"""

from __future__ import annotations

from pathlib import Path

import pytest

from amfv_decomposer.base import BaseDecomposer, RecordResult, parse_claims
from evaluate import DECOMPOSER_REGISTRY, build_output_dir, compute_metrics, make_parser


class TestBuildOutputDir:
    """Versioned output path construction."""

    def test_basic_path(self):
        """Path is <base>/<model shortname>/<dataset>/<timestamp>."""
        p = build_output_dir(Path("results"), "Qwen/Qwen3-8B", False, "AskDocs", "20260609_120000")
        assert p == Path("results/Qwen3-8B/AskDocs/20260609_120000")

    def test_thinking_suffix(self):
        """Thinking mode appends -think to the model directory."""
        p = build_output_dir(Path("results"), "Qwen/Qwen3-8B", True, "AskDocs", "20260609_120000")
        assert p == Path("results/Qwen3-8B-think/AskDocs/20260609_120000")

    def test_no_org_prefix(self):
        """Model ids without an org prefix are used as-is."""
        p = build_output_dir(Path("results"), "gpt-oss-120b", False, "AskDocs", "20260609_120000")
        assert p == Path("results/gpt-oss-120b/AskDocs/20260609_120000")

    def test_absolute_base(self):
        """Absolute base directories are preserved."""
        p = build_output_dir(Path("/data/results"), "openai/gpt-oss-120b", False, "AskDocs", "ts")
        assert p == Path("/data/results/gpt-oss-120b/AskDocs/ts")

    def test_thinking_no_org_prefix(self):
        """-think suffix also applies to bare model ids."""
        p = build_output_dir(Path("results"), "gpt-oss-120b", True, "AskDocs", "ts")
        assert p == Path("results/gpt-oss-120b-think/AskDocs/ts")

    def test_deeply_namespaced_model(self):
        """Only the last path segment of the model id is kept."""
        p = build_output_dir(Path("r"), "org/team/model-7b", False, "ds", "ts")
        assert p == Path("r/model-7b/ds/ts")


class TestMakeParser:
    """CLI argument parsing."""

    def test_data_required(self):
        """--data is mandatory."""
        with pytest.raises(SystemExit):
            make_parser().parse_args([])

    def test_defaults(self):
        """Defaults match documented behavior."""
        args = make_parser().parse_args(["--data", "AskDocs.jsonl"])
        assert args.enable_thinking is False
        assert args.output_base == Path("results")
        assert args.run_label is None
        assert args.text_key == "response"
        assert args.context_key is None
        assert args.max_records is None

    def test_vllm_decomposers_by_default(self):
        """Default decomposers are the vLLM-backed three; veriscore_original is opt-in."""
        args = make_parser().parse_args(["--data", "f.jsonl"])
        assert args.decomposers == ["factscore", "medscore", "veriscore"]

    def test_enable_thinking(self):
        """--enable-thinking sets the flag."""
        args = make_parser().parse_args(["--data", "f.jsonl", "--enable-thinking"])
        assert args.enable_thinking is True

    def test_run_label(self):
        """--run-label is stored verbatim."""
        args = make_parser().parse_args(
            ["--data", "f.jsonl", "--run-label", "SYX/mistral_based_claim_extractor"]
        )
        assert args.run_label == "SYX/mistral_based_claim_extractor"

    def test_decomposers_subset(self):
        """--decomposers accepts a subset."""
        args = make_parser().parse_args(["--data", "f.jsonl", "--decomposers", "factscore", "medscore"])
        assert args.decomposers == ["factscore", "medscore"]

    def test_veriscore_original_opt_in(self):
        """veriscore_original can be selected explicitly."""
        args = make_parser().parse_args(["--data", "f.jsonl", "--decomposers", "veriscore_original"])
        assert args.decomposers == ["veriscore_original"]

    def test_invalid_decomposer(self):
        """Unknown decomposer names are rejected."""
        with pytest.raises(SystemExit):
            make_parser().parse_args(["--data", "f.jsonl", "--decomposers", "nonexistent"])

    def test_output_base(self):
        """--output overrides the base results directory."""
        args = make_parser().parse_args(["--data", "f.jsonl", "--output", "/tmp/results"])
        assert args.output_base == Path("/tmp/results")

    def test_max_records(self):
        """--max-records is parsed as int."""
        args = make_parser().parse_args(["--data", "f.jsonl", "--max-records", "5"])
        assert args.max_records == 5


class TestRegistry:
    """Decomposer registry invariants."""

    def test_every_decomposer_declares_backend(self):
        """Each registered decomposer declares a known backend."""
        for cls in DECOMPOSER_REGISTRY.values():
            assert cls.backend in ("vllm", "hf")

    def test_hf_decomposers_declare_model_id(self):
        """HF-backed decomposers must carry the model to load."""
        for cls in DECOMPOSER_REGISTRY.values():
            if cls.backend == "hf":
                assert cls.model_id


class TestParseClaims:
    """Claim parsing, including boilerplate observed in real runs."""

    def test_plain_claims(self):
        """Dash-prefixed claims are extracted and cleaned."""
        text = "- He made his acting debut in the film.\n- The film was released in 1992."
        assert parse_claims(text) == [
            "He made his acting debut in the film.",
            "The film was released in 1992.",
        ]

    def test_numbered_lists(self):
        """Numbered list markers are stripped."""
        assert parse_claims("1. First claim.\n2) Second claim.") == ["First claim.", "Second claim."]

    def test_no_claim_phrases(self):
        """'No verifiable claim' sentinel outputs yield no claims."""
        assert parse_claims("No verifiable claim.") == []
        assert parse_claims("- No verifiable claim") == []

    # The cases below are literal boilerplate observed in the Qwen3-8B and
    # gpt-oss-120b AskDocs runs (2026-06-09), which inflated FActScore
    # claims/response by ~18% before being filtered.
    def test_skips_here_is_preamble(self):
        """Preamble lines like 'Here is the breakdown...' are not claims."""
        assert parse_claims("Here is the breakdown of the sentence into independent facts:") == []

    def test_skips_bold_header_lines(self):
        """Markdown header lines are not claims."""
        assert parse_claims("**Independent Facts:**") == []
        assert parse_claims("**Sentence:**") == []
        assert parse_claims("**Independent facts extracted from the sentence**") == []

    def test_skips_lines_ending_with_colon(self):
        """Any line ending in a colon is treated as formatting."""
        assert parse_claims("The facts in this sentence are:") == []

    def test_unwraps_bold_claims(self):
        """A fully bolded claim keeps its text without the markup."""
        assert parse_claims("**Rabies is fatal.**") == ["Rabies is fatal."]

    def test_mixed_output(self):
        """Boilerplate is dropped while real claims survive."""
        text = (
            "Here is the breakdown of the sentence into independent facts:\n"
            "**Independent Facts:**\n"
            "- Rabies has a long incubation period.\n"
            "- The incubation period ranges from 1-3 months.\n"
        )
        assert parse_claims(text) == [
            "Rabies has a long incubation period.",
            "The incubation period ranges from 1-3 months.",
        ]


class _FakeDecomposer(BaseDecomposer):
    """Echoes one request per sentence; _generate is stubbed out."""

    default_context_key = "question"

    def build_requests(self, text: str, sentences: list[str], context: str) -> list:
        """Return one synthetic request string per sentence."""
        return [f"{context}|{sent}" for sent in sentences]

    def _generate(self, requests: list) -> list[str]:
        return [f"- claim from {req}" for req in requests]


class TestDecomposeBatch:
    """Shared decompose_batch pipeline (request flattening and regrouping)."""

    def test_groups_outputs_per_record(self):
        """Flat outputs are regrouped to the records that produced them."""
        records = [
            {"response": "First sentence. Second sentence.", "question": "q1"},
            {"response": "Only sentence.", "question": "q2"},
        ]
        results = _FakeDecomposer().decompose_batch(records)
        assert len(results) == 2
        assert results[0].claims == [
            "claim from q1|First sentence.",
            "claim from q1|Second sentence.",
        ]
        assert results[0].n_sentences == 2
        assert results[1].claims == ["claim from q2|Only sentence."]
        assert len(results[1].raw_outputs) == 1

    def test_empty_text_yields_empty_result(self):
        """Records with no sentences yield an empty result, not an error."""
        results = _FakeDecomposer().decompose_batch([{"response": "", "question": "q"}])
        assert results == [RecordResult(claims=[], raw_outputs=[], n_sentences=0)]

    def test_context_key_override(self):
        """An explicit context_key wins over the class default."""
        records = [{"response": "A sentence.", "question": "q", "title": "t"}]
        results = _FakeDecomposer().decompose_batch(records, context_key="title")
        assert results[0].claims == ["claim from t|A sentence."]

    def test_decompose_single(self):
        """The single-text convenience API matches the batch pipeline."""
        claims = _FakeDecomposer().decompose("One sentence.", context="ctx")
        assert claims == ["claim from ctx|One sentence."]


class TestComputeMetrics:
    """Metric aggregation over RecordResults."""

    def test_basic(self):
        """Counts, rates, and ratios are computed over all records."""
        results = [
            RecordResult(claims=["a", "b"], raw_outputs=["r1"], n_sentences=2),
            RecordResult(claims=[], raw_outputs=["r2"], n_sentences=1),
        ]
        m = compute_metrics(results)
        assert m["n_records"] == 2
        assert m["claims_per_response"] == 1.0
        assert m["claims_per_sentence"] == pytest.approx(2 / 3)
        assert m["zero_claim_rate"] == 0.5
        assert m["total_claims"] == 2
        assert m["total_sentences"] == 3

    def test_empty(self):
        """An empty result list does not divide by zero."""
        m = compute_metrics([])
        assert m["n_records"] == 0
        assert m["claims_per_response"] == 0
