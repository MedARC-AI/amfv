"""Seed isolated recovery journeys in the disposable browser-test database."""

import json
import os
import sys
from uuid import uuid4

from app.core.db import engine
from app.core.security import get_password_hash
from app.models import Dataset, EvalFact, EvalItem, FactDecompReview, ReviewTask, User
from sqlmodel import Session, col, func, select

if not os.environ.get("SQLITE_DATABASE_URL", "").startswith("sqlite:////tmp/amfv-"):
    raise RuntimeError("Fact fixtures require the disposable E2E database")

args = json.load(sys.stdin)
with Session(engine) as session:
    if args["action"] == "deactivate":
        for dataset_id in args["datasets"]:
            dataset = session.get(Dataset, dataset_id)
            dataset.is_active = False
            session.add(dataset)
        session.commit()
        print("{}")
    elif args["action"] == "task_state":
        task = session.get(ReviewTask, args["task_id"])
        count = session.exec(
            select(func.count()).select_from(FactDecompReview).where(col(FactDecompReview.task_id) == task.id)
        ).one()
        print(json.dumps({"count": count, "labels_count": task.labels_count}))
    elif args["action"] == "add_task":
        original_task = session.get(ReviewTask, args["task_id"])
        original = session.get(EvalItem, original_task.item_a_id)
        item = EvalItem(
            dataset_id=original.dataset_id,
            eval_type=original.eval_type,
            source=original.source,
            author_user_id=original.author_user_id,
            prompt_text=original.prompt_text,
            item_metadata=original.item_metadata,
            status="ACTIVE",
        )
        session.add(item)
        session.flush()
        for fact in session.exec(select(EvalFact).where(col(EvalFact.item_id) == original.id)).all():
            session.add(
                EvalFact(item_id=item.id, position=fact.position, fact_text=fact.fact_text, polarity=fact.polarity)
            )
        task = ReviewTask(dataset_id=item.dataset_id, item_a_id=item.id)
        session.add(task)
        session.commit()
        print(json.dumps({"tasks": [task.id]}))
    elif args["action"] == "deactivate_task":
        task = session.get(ReviewTask, args["task_id"])
        task.is_active = False
        session.add(task)
        session.commit()
        print("{}")
    else:
        suffix = str(uuid4())
        reviewer = User(
            email=f"review-{suffix}@example.com",
            full_name="Recovery Reviewer",
            hashed_password=get_password_hash("recovery-password"),
            profile_completed=True,
        )
        author = User(
            email=f"author-{suffix}@example.com",
            full_name="Recovery Author",
            hashed_password=get_password_hash("recovery-password"),
            profile_completed=True,
        )
        session.add_all([reviewer, author])
        session.flush()
        # Existing work is already reviewed by this fixture identity. New tasks remain eligible.
        for task in session.exec(select(ReviewTask)).all():
            session.add(
                FactDecompReview(
                    task_id=task.id, user_id=reviewer.id, item_revision=1, ratings={}, source="e2e_fixture"
                )
            )
        datasets = []
        tasks = []
        for i, mode in enumerate(args.get("modes", ["authored", "model"])):
            dataset = Dataset(
                name=f"recovery-{suffix}-{i}",
                display_name=f"Recovery {suffix} {'A' if i == 0 else 'B'}",
                eval_type="FACT_DECOMP",
            )
            session.add(dataset)
            session.flush()
            annotation = {
                "claim": "Alpha is true.",
                "label": "vital",
                "spans": [{"start": 0, "end": 14, "text": "Alpha is true."}],
            }
            metadata = (
                {
                    "schema_version": 2,
                    "review_mode": "MODEL_LABEL_CORRECTION",
                    "case_id": suffix,
                    "arm_id": f"arm-{i}",
                    "canonical_row_sha256": "b" * 64,
                    "generator": {
                        "model_id": "e2e",
                        "prompt_text": "Test",
                        "pydantic_ai_version": "2.33.0",
                        "generation": {},
                    },
                    "ordered_claim_annotations": [annotation],
                }
                if mode == "model"
                else None
            )
            item = EvalItem(
                dataset_id=dataset.id,
                eval_type="FACT_DECOMP",
                source="LLM" if metadata else "HUMAN",
                author_user_id=author.id,
                prompt_text="Alpha is true. Beta is false.",
                item_metadata=metadata,
                status="ACTIVE",
            )
            session.add(item)
            session.flush()
            session.add(EvalFact(item_id=item.id, position=0, fact_text="Alpha is true.", polarity="SHOULD_LIST"))
            task = ReviewTask(dataset_id=dataset.id, item_a_id=item.id)
            session.add(task)
            session.flush()
            datasets.append({"id": dataset.id, "name": dataset.display_name})
            tasks.append(task.id)
        reviewer.fact_decomp_dataset_id = datasets[0]["id"]
        session.add(reviewer)
        session.commit()
        print(
            json.dumps(
                {
                    "reviewer": reviewer.email,
                    "author": author.email,
                    "user_id": str(reviewer.id),
                    "datasets": datasets,
                    "tasks": tasks,
                }
            )
        )
