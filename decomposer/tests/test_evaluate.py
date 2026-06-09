"""Tests for evaluate.py — path construction and CLI arg parsing.

These tests require no running server; they exercise only pure-Python logic.
Run with: pytest tests/
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evaluate import build_output_dir, make_parser


class TestBuildOutputDir:
    def test_basic_path(self):
        p = build_output_dir(Path("results"), "Qwen/Qwen3-8B", False, "AskDocs", "20260609_120000")
        assert p == Path("results/Qwen3-8B/AskDocs/20260609_120000")

    def test_thinking_suffix(self):
        p = build_output_dir(Path("results"), "Qwen/Qwen3-8B", True, "AskDocs", "20260609_120000")
        assert p == Path("results/Qwen3-8B-think/AskDocs/20260609_120000")

    def test_no_org_prefix(self):
        p = build_output_dir(Path("results"), "gpt-oss-120b", False, "AskDocs", "20260609_120000")
        assert p == Path("results/gpt-oss-120b/AskDocs/20260609_120000")

    def test_absolute_base(self):
        p = build_output_dir(Path("/data/results"), "openai/gpt-oss-120b", False, "AskDocs", "ts")
        assert p == Path("/data/results/gpt-oss-120b/AskDocs/ts")

    def test_thinking_no_org_prefix(self):
        p = build_output_dir(Path("results"), "gpt-oss-120b", True, "AskDocs", "ts")
        assert p == Path("results/gpt-oss-120b-think/AskDocs/ts")

    def test_deeply_namespaced_model(self):
        p = build_output_dir(Path("r"), "org/team/model-7b", False, "ds", "ts")
        assert p == Path("r/model-7b/ds/ts")


class TestMakeParser:
    def test_data_required(self):
        with pytest.raises(SystemExit):
            make_parser().parse_args([])

    def test_defaults(self):
        args = make_parser().parse_args(["--data", "AskDocs.jsonl"])
        assert args.enable_thinking is False
        assert args.output_base == Path("results")
        assert args.model_name is None
        assert args.text_key == "response"
        assert args.context_key is None
        assert args.max_records is None

    def test_all_decomposers_by_default(self):
        args = make_parser().parse_args(["--data", "f.jsonl"])
        assert set(args.decomposers) == {"factscore", "medscore", "veriscore", "veriscore_original"}

    def test_enable_thinking(self):
        args = make_parser().parse_args(["--data", "f.jsonl", "--enable-thinking"])
        assert args.enable_thinking is True

    def test_model_name_override(self):
        args = make_parser().parse_args(["--data", "f.jsonl", "--model-name", "SYX/mistral_based_claim_extractor"])
        assert args.model_name == "SYX/mistral_based_claim_extractor"

    def test_decomposers_subset(self):
        args = make_parser().parse_args(["--data", "f.jsonl", "--decomposers", "factscore", "medscore"])
        assert args.decomposers == ["factscore", "medscore"]

    def test_invalid_decomposer(self):
        with pytest.raises(SystemExit):
            make_parser().parse_args(["--data", "f.jsonl", "--decomposers", "nonexistent"])

    def test_output_base(self):
        args = make_parser().parse_args(["--data", "f.jsonl", "--output", "/tmp/results"])
        assert args.output_base == Path("/tmp/results")

    def test_max_records(self):
        args = make_parser().parse_args(["--data", "f.jsonl", "--max-records", "5"])
        assert args.max_records == 5
