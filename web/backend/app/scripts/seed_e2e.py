from __future__ import annotations

import argparse
import json
import sys

from sqlmodel import Session, col, select

from app.core.db import engine, init_db
from app.core.security import get_password_hash
from app.models import (
    AuthorKind,
    Chunk,
    Dataset,
    Document,
    EvalFact,
    EvalItem,
    EvalType,
    FactPolarity,
    ItemSource,
    ItemStatus,
    PooledCandidate,
    ReviewTask,
    User,
    UserRole,
)
from app.services.documents import create_document_with_chunks


def _get_or_create_user(session: Session) -> User:
    email = "e2e-user@example.com"
    user = session.exec(select(User).where(col(User.email) == email)).first()
    if user:
        assert user.id is not None
        return user

    user = User(
        email=email,
        full_name="E2E User",
        role=UserRole.user,
        hashed_password=get_password_hash("e2e-password"),
        profile_completed=True,
    )
    session.add(user)
    session.flush()
    assert user.id is not None
    return user


def create_browser_test_user(*, email: str, password: str) -> User:
    """Create one browser-test identity directly in the disposable database."""
    with Session(engine) as session:
        user = session.exec(select(User).where(col(User.email) == email)).first()
        if user is None:
            user = User(
                email=email,
                full_name="Test User",
                role=UserRole.user,
                hashed_password=get_password_hash(password),
                profile_completed=True,
            )
            session.add(user)
            session.commit()
            session.refresh(user)
        assert user.id is not None
        return user


def _get_or_create_dataset(
    session: Session,
    *,
    name: str,
    display_name: str,
    eval_type: EvalType,
) -> Dataset:
    dataset = session.exec(select(Dataset).where(col(Dataset.name) == name)).first()
    if dataset:
        assert dataset.id is not None
        return dataset

    dataset = Dataset(name=name, display_name=display_name, eval_type=eval_type)
    session.add(dataset)
    session.flush()
    assert dataset.id is not None
    return dataset


def _get_or_create_document(
    session: Session,
    *,
    dataset_id: int,
    external_id: str,
    title: str,
    content: str,
) -> Document:
    existing = session.exec(
        select(Document).where(
            col(Document.dataset_id) == dataset_id,
            col(Document.external_id) == external_id,
        )
    ).first()
    if existing:
        assert existing.id is not None
        return existing
    document = create_document_with_chunks(
        session,
        dataset_id=dataset_id,
        title=title,
        content=content,
        external_id=external_id,
    )
    assert document.id is not None
    return document


def _first_chunk(session: Session, *, document_id: int) -> Chunk:
    chunk = session.exec(
        select(Chunk).where(col(Chunk.document_id) == document_id).order_by(col(Chunk.position))
    ).first()
    if chunk is None:
        raise RuntimeError(f"Seed document {document_id} has no chunks")
    assert chunk.id is not None
    return chunk


def _get_or_create_retrieval_item(
    session: Session,
    *,
    dataset: Dataset,
    user: User,
    chunk: Chunk,
) -> EvalItem:
    assert dataset.id is not None
    assert chunk.id is not None
    item = session.exec(
        select(EvalItem).where(
            col(EvalItem.dataset_id) == dataset.id,
            col(EvalItem.external_id) == "e2e-retrieval-item",
        )
    ).first()
    if item:
        assert item.id is not None
        return item

    answer = "Baker"
    start = chunk.text.index(answer)
    item = EvalItem(
        dataset_id=dataset.id,
        external_id="e2e-retrieval-item",
        eval_type=EvalType.RETRIEVAL,
        category="VERBATIM",
        document_id=chunk.document_id,
        source=ItemSource.HUMAN,
        author_kind=AuthorKind.HUMAN_LAY,
        author_user_id=user.id,
        prompt_text="Which selected answer appears in the E2E document?",
        expected_answer=answer,
        evidence_spans=[
            {
                "chunk_id": chunk.id,
                "start": start,
                "end": start + len(answer),
                "text": answer,
                "kind": "gold",
            }
        ],
        gold_chunk_ids=[chunk.id],
        status=ItemStatus.ACTIVE,
    )
    session.add(item)
    session.flush()
    assert item.id is not None
    return item


def _get_or_create_submitted_retrieval_item(
    session: Session,
    *,
    dataset: Dataset,
    user: User,
    chunk: Chunk,
) -> EvalItem:
    assert dataset.id is not None
    assert chunk.id is not None
    item = session.exec(
        select(EvalItem).where(
            col(EvalItem.dataset_id) == dataset.id,
            col(EvalItem.external_id) == "e2e-submitted-retrieval-item",
        )
    ).first()
    if item:
        assert item.id is not None
        return item

    answer = "Baker"
    start = chunk.text.index(answer)
    item = EvalItem(
        dataset_id=dataset.id,
        external_id="e2e-submitted-retrieval-item",
        eval_type=EvalType.RETRIEVAL,
        category="VERBATIM",
        document_id=chunk.document_id,
        source=ItemSource.HUMAN,
        author_kind=AuthorKind.HUMAN_LAY,
        author_user_id=user.id,
        prompt_text="Which answer should admin moderation approve?",
        expected_answer=answer,
        evidence_spans=[
            {
                "chunk_id": chunk.id,
                "start": start,
                "end": start + len(answer),
                "text": answer,
                "kind": "gold",
            }
        ],
        gold_chunk_ids=[chunk.id],
        status=ItemStatus.SUBMITTED,
    )
    session.add(item)
    session.flush()
    assert item.id is not None
    return item


def _get_or_create_candidate(
    session: Session,
    *,
    dataset: Dataset,
    item: EvalItem,
    chunk: Chunk,
) -> None:
    assert dataset.id is not None
    assert item.id is not None
    assert chunk.id is not None
    existing = session.exec(
        select(PooledCandidate).where(
            col(PooledCandidate.dataset_id) == dataset.id,
            col(PooledCandidate.item_id) == item.id,
            col(PooledCandidate.chunk_id) == chunk.id,
        )
    ).first()
    if existing:
        return

    session.add(
        PooledCandidate(
            dataset_id=dataset.id,
            item_id=item.id,
            chunk_id=chunk.id,
            systems=["e2e-system"],
            ranks={"e2e-system": 1},
            is_calibration=True,
            reference_grade=3,
        )
    )
    session.flush()


def _get_or_create_fact_item(
    session: Session,
    *,
    dataset: Dataset,
    user: User,
    chunk: Chunk,
) -> EvalItem:
    assert dataset.id is not None
    assert chunk.id is not None
    item = session.exec(
        select(EvalItem).where(
            col(EvalItem.dataset_id) == dataset.id,
            col(EvalItem.external_id) == "e2e-fact-item",
        )
    ).first()
    if item:
        assert item.id is not None
        return item

    item = EvalItem(
        dataset_id=dataset.id,
        external_id="e2e-fact-item",
        eval_type=EvalType.FACT_DECOMP,
        document_id=chunk.document_id,
        source=ItemSource.HUMAN,
        author_kind=AuthorKind.HUMAN_LAY,
        author_user_id=user.id,
        prompt_text="Baker appears in the E2E source. Cafe\u0301 appears too.",
        evidence_spans=[
            {
                "chunk_id": chunk.id,
                "start": 0,
                "end": len("Baker"),
                "text": "Baker",
                "kind": "provenance",
            }
        ],
        gold_chunk_ids=[chunk.id],
        status=ItemStatus.ACTIVE,
    )
    session.add(item)
    session.flush()
    assert item.id is not None

    session.add(
        EvalFact(
            item_id=item.id,
            fact_uuid="e2e-fact-should-list",
            fact_text="Baker appears in the E2E source.",
            polarity=FactPolarity.SHOULD_LIST,
            position=0,
        )
    )
    session.add(
        EvalFact(
            item_id=item.id,
            fact_uuid="e2e-fact-should-not-list",
            fact_text="A distractor answer should not be listed.",
            polarity=FactPolarity.SHOULD_NOT_LIST,
            position=1,
        )
    )
    session.flush()
    return item


def _get_or_create_review_task(
    session: Session,
    *,
    dataset: Dataset,
    item: EvalItem,
) -> None:
    assert dataset.id is not None
    assert item.id is not None
    existing = session.exec(
        select(ReviewTask).where(col(ReviewTask.item_a_id) == item.id)
    ).first()
    if existing:
        return

    session.add(
        ReviewTask(
            dataset_id=dataset.id,
            item_a_id=item.id,
            priority_score=1.0,
            is_active=True,
        )
    )
    session.flush()


def seed() -> None:
    with Session(engine) as session:
        init_db(session)
        user = _get_or_create_user(session)
        retrieval_dataset = _get_or_create_dataset(
            session,
            name="e2e-retrieval",
            display_name="E2E Retrieval",
            eval_type=EvalType.RETRIEVAL,
        )
        fact_dataset = _get_or_create_dataset(
            session,
            name="e2e-fact-decomposition",
            display_name="E2E Fact Decomposition",
            eval_type=EvalType.FACT_DECOMP,
        )
        assert retrieval_dataset.id is not None
        assert fact_dataset.id is not None

        retrieval_document = _get_or_create_document(
            session,
            dataset_id=retrieval_dataset.id,
            external_id="e2e-retrieval-doc",
            title="E2E Retrieval Source",
            content="The selected answer is Baker.\n\nEmoji 😀 and Cafe\u0301 text are present.",
        )
        fact_document = _get_or_create_document(
            session,
            dataset_id=fact_dataset.id,
            external_id="e2e-fact-doc",
            title="E2E Fact Source",
            content="Baker appears in the E2E source.\n\nCafe\u0301 and emoji 😀 are available for offset checks.",
        )
        assert retrieval_document.id is not None
        assert fact_document.id is not None

        retrieval_chunk = _first_chunk(session, document_id=retrieval_document.id)
        fact_chunk = _first_chunk(session, document_id=fact_document.id)
        assert retrieval_chunk.id is not None
        assert fact_chunk.id is not None
        retrieval_item = _get_or_create_retrieval_item(
            session,
            dataset=retrieval_dataset,
            user=user,
            chunk=retrieval_chunk,
        )
        _get_or_create_submitted_retrieval_item(
            session,
            dataset=retrieval_dataset,
            user=user,
            chunk=retrieval_chunk,
        )
        _get_or_create_candidate(
            session,
            dataset=retrieval_dataset,
            item=retrieval_item,
            chunk=retrieval_chunk,
        )
        fact_item = _get_or_create_fact_item(
            session,
            dataset=fact_dataset,
            user=user,
            chunk=fact_chunk,
        )
        _get_or_create_review_task(session, dataset=fact_dataset, item=fact_item)
        session.commit()


def main() -> None:
    """Seed the standard E2E data or create one requested browser identity."""
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    create_user_parser = subparsers.add_parser("create-user")
    create_user_parser.add_argument("--email", required=True)
    create_user_parser.add_argument("--password", required=True)
    args = parser.parse_args()

    if args.command == "create-user":
        user = create_browser_test_user(email=args.email, password=args.password)
        sys.stdout.write(
            json.dumps(
                {
                    "id": str(user.id),
                    "email": user.email,
                    "full_name": user.full_name,
                }
            )
            + "\n"
        )
        return

    seed()


if __name__ == "__main__":
    main()
