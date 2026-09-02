from sqlmodel import Session, SQLModel, create_engine

from app.models import (
    Chunk,
    Dataset,
    Document,
    EvalFact,
    EvalItem,
    EvalType,
    FactPolarity,
    ItemSource,
    ItemStatus,
    RetrievalCategory,
)
from app.schemas import EvidenceSpan
from app.services.documents import (
    EvidenceSpanValidationError,
    documents_for_chunks,
    resolve_item_chunks,
    validate_evidence_spans,
)
from app.services.validation import split_paragraphs, validate_item


def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_verbatim_answer_must_appear_in_document() -> None:
    with session() as db:
        dataset = Dataset(
            name="retrieval", display_name="Retrieval", eval_type=EvalType.RETRIEVAL
        )
        db.add(dataset)
        db.flush()
        document = Document(
            dataset_id=dataset.id,
            external_id="doc-1",
            title="Doc",
            content="The answer is exactly here.",
            paragraphs=split_paragraphs("The answer is exactly here."),
        )
        db.add(document)
        db.flush()
        item = EvalItem(
            dataset_id=dataset.id,
            eval_type=EvalType.RETRIEVAL,
            category=RetrievalCategory.VERBATIM,
            document_id=document.id,
            source=ItemSource.HUMAN,
            prompt_text="Where is the answer?",
            expected_answer="not present",
            status=ItemStatus.SUBMITTED,
        )
        db.add(item)
        db.flush()

        result = validate_item(db, item, document=document)

        assert not result.ok
        assert any("highlighted answer text" in message for message in result.blocking)
        assert any("word-for-word" in message for message in result.blocking)


def test_fact_decomp_requires_both_polarities() -> None:
    with session() as db:
        dataset = Dataset(
            name="facts", display_name="Facts", eval_type=EvalType.FACT_DECOMP
        )
        db.add(dataset)
        db.flush()
        item = EvalItem(
            dataset_id=dataset.id,
            eval_type=EvalType.FACT_DECOMP,
            source=ItemSource.HUMAN,
            prompt_text="A source statement.",
            status=ItemStatus.SUBMITTED,
        )
        db.add(item)
        db.flush()
        facts = [
            EvalFact(
                item_id=item.id,
                fact_text="A listed fact.",
                polarity=FactPolarity.SHOULD_LIST,
                position=0,
            )
        ]

        result = validate_item(db, item, facts=facts)

        assert not result.ok
        assert any("SHOULD_NOT_LIST" in message for message in result.blocking)


def test_fact_decomp_warns_on_duplicate_and_context_dependent_facts() -> None:
    with session() as db:
        dataset = Dataset(
            name="fact-warnings",
            display_name="Fact Warnings",
            eval_type=EvalType.FACT_DECOMP,
        )
        db.add(dataset)
        db.flush()
        item = EvalItem(
            dataset_id=dataset.id,
            eval_type=EvalType.FACT_DECOMP,
            source=ItemSource.HUMAN,
            prompt_text="A source statement.",
            status=ItemStatus.SUBMITTED,
        )
        db.add(item)
        db.flush()
        facts = [
            EvalFact(
                item_id=item.id,
                fact_text="This drug reduced symptoms.",
                polarity=FactPolarity.SHOULD_LIST,
                position=0,
            ),
            EvalFact(
                item_id=item.id,
                fact_text="Noise answer choice.",
                polarity=FactPolarity.SHOULD_NOT_LIST,
                position=1,
            ),
            EvalFact(
                item_id=item.id,
                fact_text="Noise answer choice.",
                polarity=FactPolarity.SHOULD_NOT_LIST,
                position=2,
            ),
        ]

        result = validate_item(db, item, facts=facts)

        assert result.ok
        assert any("duplicate" in message for message in result.warnings)
        assert any("independently verifiable" in message for message in result.warnings)


def test_evidence_span_validation_uses_python_code_point_offsets() -> None:
    with session() as db:
        dataset = Dataset(
            name="span", display_name="Span", eval_type=EvalType.RETRIEVAL
        )
        db.add(dataset)
        db.flush()
        document = Document(
            dataset_id=dataset.id,
            external_id="doc-span",
            title="Doc",
            content="Alpha 🧪 answer\n\nCafe\u0301 marker",
            paragraphs=split_paragraphs("Alpha 🧪 answer\n\nCafe\u0301 marker"),
        )
        db.add(document)
        db.flush()
        chunk = Chunk(
            dataset_id=dataset.id,
            document_id=document.id,
            external_id="doc-span-chunk-0",
            text="Alpha 🧪 answer",
            position=0,
        )
        combining_chunk = Chunk(
            dataset_id=dataset.id,
            document_id=document.id,
            external_id="doc-span-chunk-1",
            text="Cafe\u0301 marker",
            position=1,
        )
        db.add(chunk)
        db.add(combining_chunk)
        db.flush()

        emoji_start = chunk.text.index("🧪")
        emoji_text = "🧪 answer"
        combining_text = "Cafe\u0301"

        chunks = validate_evidence_spans(
            db,
            dataset_id=dataset.id,
            spans=[
                EvidenceSpan(
                    chunk_id=chunk.id,
                    start=emoji_start,
                    end=emoji_start + len(emoji_text),
                    text=emoji_text,
                ),
                EvidenceSpan(
                    chunk_id=combining_chunk.id,
                    start=0,
                    end=len(combining_text),
                    text=combining_text,
                ),
            ],
        )

        assert [selected.id for selected in chunks] == [chunk.id, combining_chunk.id]


def test_evidence_span_validation_rejects_stale_text_offsets_and_dataset_mismatch() -> (
    None
):
    with session() as db:
        dataset = Dataset(
            name="span-a", display_name="Span A", eval_type=EvalType.RETRIEVAL
        )
        other_dataset = Dataset(
            name="span-b", display_name="Span B", eval_type=EvalType.RETRIEVAL
        )
        db.add(dataset)
        db.add(other_dataset)
        db.flush()
        document = Document(
            dataset_id=dataset.id,
            external_id="doc-a",
            title="Doc A",
            content="Current answer.",
            paragraphs=[],
        )
        other_document = Document(
            dataset_id=other_dataset.id,
            external_id="doc-b",
            title="Doc B",
            content="Wrong dataset.",
            paragraphs=[],
        )
        db.add(document)
        db.add(other_document)
        db.flush()
        chunk = Chunk(
            dataset_id=dataset.id,
            document_id=document.id,
            external_id="doc-a-chunk-0",
            text="Current answer.",
            position=0,
        )
        other_chunk = Chunk(
            dataset_id=other_dataset.id,
            document_id=other_document.id,
            external_id="doc-b-chunk-0",
            text="Wrong dataset.",
            position=0,
        )
        db.add(chunk)
        db.add(other_chunk)
        db.flush()

        try:
            validate_evidence_spans(
                db,
                dataset_id=dataset.id,
                spans=[
                    EvidenceSpan(chunk_id=chunk.id, start=0, end=7, text="Stale!!"),
                    EvidenceSpan(chunk_id=other_chunk.id, start=0, end=5, text="Wrong"),
                ],
            )
        except EvidenceSpanValidationError as exc:
            assert any("does not match" in message for message in exc.messages)
            assert any("another dataset" in message for message in exc.messages)
        else:
            raise AssertionError("invalid evidence spans should fail validation")


def test_multi_document_validation_uses_resolved_chunks() -> None:
    with session() as db:
        dataset = Dataset(
            name="multi-doc", display_name="Multi Doc", eval_type=EvalType.RETRIEVAL
        )
        db.add(dataset)
        db.flush()
        first_document = Document(
            dataset_id=dataset.id,
            external_id="multi-a",
            title="A",
            content="First evidence.",
            paragraphs=[],
        )
        second_document = Document(
            dataset_id=dataset.id,
            external_id="multi-b",
            title="B",
            content="Second evidence.",
            paragraphs=[],
        )
        db.add(first_document)
        db.add(second_document)
        db.flush()
        first_chunk = Chunk(
            dataset_id=dataset.id,
            document_id=first_document.id,
            external_id="multi-a-chunk-0",
            text="First evidence.",
            position=0,
        )
        second_chunk = Chunk(
            dataset_id=dataset.id,
            document_id=second_document.id,
            external_id="multi-b-chunk-0",
            text="Second evidence.",
            position=0,
        )
        db.add(first_chunk)
        db.add(second_chunk)
        db.flush()
        item = EvalItem(
            dataset_id=dataset.id,
            eval_type=EvalType.RETRIEVAL,
            category=RetrievalCategory.MULTI_DOCUMENT,
            source=ItemSource.HUMAN,
            prompt_text="What requires two documents?",
            expected_answer="Both pieces",
            evidence_spans=[
                {"chunk_id": first_chunk.id, "start": 0, "end": 5, "text": "First"},
                {"chunk_id": second_chunk.id, "start": 0, "end": 6, "text": "Second"},
            ],
            gold_chunk_ids=[first_chunk.id, second_chunk.id],
            status=ItemStatus.SUBMITTED,
        )
        db.add(item)
        db.flush()

        chunks = resolve_item_chunks(db, item)
        documents = documents_for_chunks(db, chunks)
        result = validate_item(db, item, chunks=chunks)

        assert result.ok
        assert {document.id for document in documents} == {
            first_document.id,
            second_document.id,
        }
