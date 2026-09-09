from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session

from app.core.config import settings
from app.core.db import engine
from app.models import (
    Dataset,
    EvalFact,
    EvalItem,
    EvalType,
    FactDecompReview,
    FactPolarity,
    ItemSource,
    ItemStatus,
    ReviewerKind,
    ReviewTask,
    User,
)


def test_admin_export_joins_model_corrections_without_private_metadata(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    model_author = User(email="export-model-author@example.com", hashed_password="x")
    reviewer = User(
        email="export-model-reviewer@example.com",
        hashed_password="x",
        reviewer_kind=ReviewerKind.human,
    )
    authored_author = User(
        email="export-authored-author@example.com", hashed_password="x"
    )
    dataset = Dataset(
        name="admin-model-export",
        display_name="Admin Model Export",
        eval_type=EvalType.FACT_DECOMP,
    )
    db.add_all([model_author, reviewer, authored_author, dataset])
    db.flush()
    model_item = EvalItem(
        dataset_id=dataset.id,
        external_id="model-export-external",
        eval_type=EvalType.FACT_DECOMP,
        source=ItemSource.LLM,
        author_user_id=model_author.id,
        prompt_text="Alpha is true. Beta is false.",
        lazy_query="What does the response say?",
        generator_name="model-name-must-not-be-used-as-provenance",
        item_metadata={
            "schema_version": 1,
            "review_mode": "MODEL_LABEL_CORRECTION",
            "case_id": "case-export",
            "arm_id": "arm-gpt-oss",
            "canonical_row_sha256": "b" * 64,
            "generator": {
                "model_id": "gpt-oss-20b",
                "model_revision": None,
                "prompt_id": "decomp-v1",
                "prompt_hash": "a" * 64,
                "pydantic_ai_version": "2.33.0",
                "generation": {"reasoning_effort": "low"},
            },
            "ordered_claim_annotations": [
                {
                    "claim": "Alpha is true.",
                    "label": "substantive",
                    "spans": [{"start": 0, "end": 14, "text": "Alpha is true."}],
                },
                {
                    "claim": "Beta is false.",
                    "label": "substantive",
                    "spans": [{"start": 15, "end": 29, "text": "Beta is false."}],
                },
            ],
        },
        status=ItemStatus.ACTIVE,
    )
    authored_item = EvalItem(
        dataset_id=dataset.id,
        eval_type=EvalType.FACT_DECOMP,
        source=ItemSource.HUMAN,
        author_user_id=authored_author.id,
        prompt_text="Authored statement.",
        status=ItemStatus.ACTIVE,
    )
    db.add_all([model_item, authored_item])
    db.flush()
    db.add_all(
        [
            EvalFact(
                item_id=model_item.id,
                fact_text="Alpha is true.",
                polarity=FactPolarity.SHOULD_LIST,
                position=0,
            ),
            EvalFact(
                item_id=model_item.id,
                fact_text="Beta is false.",
                polarity=FactPolarity.SHOULD_LIST,
                position=1,
            ),
            EvalFact(
                item_id=authored_item.id,
                fact_text="Authored statement.",
                polarity=FactPolarity.SHOULD_LIST,
                position=0,
            ),
        ]
    )
    model_task = ReviewTask(
        dataset_id=dataset.id, item_a_id=model_item.id, labels_count=1
    )
    authored_task = ReviewTask(
        dataset_id=dataset.id, item_a_id=authored_item.id, labels_count=1
    )
    db.add_all([model_task, authored_task])
    db.flush()
    db.add_all(
        [
            FactDecompReview(
                task_id=model_task.id,
                user_id=reviewer.id,
                item_revision=model_item.revision,
                reviewer_kind=ReviewerKind.human,
                source="web_model_eval",
                ratings={
                    "review_mode": "MODEL_LABEL_CORRECTION",
                    "proposed_labels": ["substantive", "substantive"],
                    "final_claims": [
                        {
                            "original_position": None,
                            "claim_text": "The response mentions alpha.",
                            "response_spans": [{"start": 0, "end": 5, "text": "Alpha"}],
                            "label": "substantive",
                        }
                    ],
                },
            ),
            FactDecompReview(
                task_id=authored_task.id,
                user_id=reviewer.id,
                item_revision=authored_item.revision,
                reviewer_kind=ReviewerKind.human,
                source="web",
                ratings={"fact_calls": ["SHOULD_LIST"]},
            ),
        ]
    )
    db.commit()

    def count_selects(url: str) -> tuple[dict, int]:
        statements: list[str] = []

        def record(
            connection: object,
            cursor: object,
            statement: str,
            parameters: object,
            context: object,
            executemany: bool,
        ) -> None:
            _ = connection, cursor, parameters, context, executemany
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        event.listen(engine, "before_cursor_execute", record)
        try:
            response = client.get(url, headers=superuser_token_headers)
        finally:
            event.remove(engine, "before_cursor_execute", record)
        return response.json(), len(statements)

    small, small_queries = count_selects(
        f"{settings.API_V1_STR}/admin/export?dataset_id={dataset.id}&limit=1"
    )
    full, full_queries = count_selects(
        f"{settings.API_V1_STR}/admin/export?dataset_id={dataset.id}&limit=100"
    )
    assert small_queries == full_queries == 11
    exported = next(row for row in full["items"] if row.get("case_id") == "case-export")
    assert exported["review_mode"] == "MODEL_LABEL_CORRECTION"
    assert exported["external_id"] == "model-export-external"
    assert exported["arm_id"] == "arm-gpt-oss"
    assert exported["user_prompt"] == "What does the response say?"
    assert exported["assistant_response"] == model_item.prompt_text
    assert exported["generator"] == {
        "model_id": "gpt-oss-20b",
        "model_revision": None,
        "prompt_id": "decomp-v1",
        "prompt_hash": "a" * 64,
        "pydantic_ai_version": "2.33.0",
        "generation": {"reasoning_effort": "low"},
    }
    assert exported["claims"] == [
        {
            "claim": "Alpha is true.",
            "position": 0,
            "spans": [{"start": 0, "end": 14, "text": "Alpha is true."}],
            "proposed_label": "substantive",
        },
        {
            "claim": "Beta is false.",
            "position": 1,
            "spans": [{"start": 15, "end": 29, "text": "Beta is false."}],
            "proposed_label": "substantive",
        },
    ]
    assert exported["correction_reviews"][0]["final_claims"]
    assert exported["review_task"]["labels_count"] == 1
    assert "reviews" not in exported["review_task"]
    assert "prompt_text" not in exported
    assert "facts" not in exported
    assert "fact_decomp_reviews" not in exported
    assert all(
        set(claim) == {"claim", "position", "spans", "proposed_label"}
        for claim in exported["claims"]
    )
    assert "api_key" not in str(exported)
    assert "raw_messages" not in str(exported)
    authored = next(
        row for row in full["items"] if row.get("prompt_text") == "Authored statement."
    )
    assert authored["review_mode"] == "AUTHORED"
    assert authored["fact_decomp_reviews"][0]["ratings"] == {
        "fact_calls": ["SHOULD_LIST"]
    }


def test_admin_export_rejects_a_page_over_the_content_budget(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    dataset = Dataset(
        name="admin-model-export-budget",
        display_name="Admin Model Export Budget",
        eval_type=EvalType.FACT_DECOMP,
    )
    db.add(dataset)
    db.flush()
    for index in range(5):
        db.add(
            EvalItem(
                dataset_id=dataset.id,
                external_id=f"budget-{index}",
                eval_type=EvalType.FACT_DECOMP,
                source=ItemSource.LLM,
                prompt_text="x" * 1_000_000,
                item_metadata={
                    "schema_version": 1,
                    "review_mode": "MODEL_LABEL_CORRECTION",
                    "case_id": f"case-{index}",
                    "arm_id": "arm-1",
                    "canonical_row_sha256": f"{index}" * 64,
                    "generator": {
                        "model_id": "gpt-oss-20b",
                        "model_revision": None,
                        "prompt_id": "decomp-v1",
                        "prompt_hash": "a" * 64,
                        "pydantic_ai_version": "2.33.0",
                        "generation": {},
                    },
                    "ordered_claim_annotations": [],
                },
                status=ItemStatus.ACTIVE,
            )
        )
    db.commit()

    response = client.get(
        f"{settings.API_V1_STR}/admin/export?dataset_id={dataset.id}&limit=5",
        headers=superuser_token_headers,
    )

    assert response.status_code == 413
    assert "content limit" in response.json()["detail"]
