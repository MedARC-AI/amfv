import { describe, expect, test } from "bun:test"

import type { AdminExportModelCorrectionItem, AdminUserMetric } from "@/client"
import {
  addedPositionBase,
  buildGradesModel,
  claimColorOwners,
  groupAddedClaims,
  normalizeClaimText,
  percent,
} from "./gradesModel"

const users: AdminUserMetric[] = [
  {
    user_id: "u-ann",
    email: "ann@example.com",
    role: "user",
    reviewer_kind: "human",
    authored_items: 0,
    fact_decomp_reviews: 1,
    retrieval_qa_reviews: 0,
    relevance_judgments: 0,
  },
  {
    user_id: "u-bo",
    email: "bo@example.com",
    role: "data_admin",
    reviewer_kind: "expert",
    authored_items: 0,
    fact_decomp_reviews: 1,
    retrieval_qa_reviews: 0,
    relevance_judgments: 0,
  },
]

const item: AdminExportModelCorrectionItem = {
  review_mode: "MODEL_LABEL_CORRECTION",
  id: 1,
  dataset_id: 1,
  eval_type: "FACT_DECOMP",
  status: "ACTIVE",
  schema_version: 2,
  external_id: "x",
  case_id: "case-1",
  arm_id: "arm-1",
  canonical_row_sha256: "0".repeat(64),
  user_prompt: "Q?",
  assistant_response: "Alpha is true. Beta is false. Gamma holds.",
  generator: {
    model_id: "model",
    model_revision: null,
    prompt_text: "p",
    pydantic_ai_version: "1",
    generation: {},
  },
  claims: [
    {
      claim: "Alpha is true.",
      position: 0,
      spans: [{ start: 0, end: 14, text: "Alpha is true." }],
      proposed_label: "vital",
    },
    {
      claim: "Beta is false.",
      position: 1,
      spans: [{ start: 15, end: 29, text: "Beta is false." }],
      proposed_label: "semi-important",
    },
  ],
  review_task: null,
  review_task_count: 1,
  correction_reviews: [
    {
      id: 1,
      user_id: "u-ann",
      item_revision: 1,
      rubric_id: "importance-v1",
      claim_reviews: [
        { position: 0, looks_good: true, label: "vital" },
        { position: 1, label: "vital" },
      ],
      human_claims: [
        {
          claim_text: "Gamma holds.",
          response_spans: [{ start: 30, end: 42, text: "Gamma holds." }],
          label: "unimportant",
        },
      ],
      coverage_checked: true,
      reviewer_kind: "human",
      source: "web_model_eval",
    },
    {
      id: 2,
      user_id: "u-bo",
      item_revision: 1,
      rubric_id: "importance-v1",
      claim_reviews: [
        { position: 0, issue: "Restates the question." },
        { position: 1, looks_good: true, label: "semi-important" },
      ],
      human_claims: [
        {
          claim_text: "Gamma holds",
          response_spans: [{ start: 30, end: 41, text: "Gamma holds" }],
          label: "semi-important",
          split_from_position: 1,
        },
      ],
      coverage_checked: true,
      reviewer_kind: "expert",
      source: "web_model_eval",
    },
  ],
}

describe("buildGradesModel", () => {
  const model = buildGradesModel([item], users, { "u-bo": "Bo Expert" })
  const task = model.tasks[0]

  test("an issue-only judgment keeps the model's grade", () => {
    const first = task.claims[0]
    expect(first.judgments.map((j) => j.effectiveLabel)).toEqual([
      "vital",
      "vital",
    ])
    expect(first.disagreement).toBe(false)
    expect(first.flagged).toBe(true)
    expect(first.labelVotes).toEqual({
      vital: 2,
      "semi-important": 0,
      unimportant: 0,
    })
    expect(first.choiceCounts).toEqual({
      looksGood: 1,
      issue: 1,
      duplicate: 0,
      multipleFacts: 0,
    })
  })

  test("a reviewer grade that differs from the model's is a disagreement", () => {
    const second = task.claims[1]
    expect(second.disagreement).toBe(true)
    expect(task.disagreeCount).toBe(1)
    expect(task.judged).toBe(2)
    expect(task.judgments).toBe(4)
    expect(model.totals.disagreements).toBe(1)
    expect(model.totals.changed).toBe(1)
    // Ann moved claim 2 from semi-important to vital: toward vital is "up".
    expect(model.totals.gradedUp).toBe(1)
    expect(model.totals.gradedDown).toBe(0)
    expect(model.totals.reviews).toBe(2)
  })

  test("reviewers splitting a claim between grades is counted separately", () => {
    expect(task.claims[0].reviewersSplit).toBe(false)
    expect(task.claims[1].reviewersSplit).toBe(true)
    expect(task.multiGraded).toBe(2)
    expect(task.splitCount).toBe(1)
    expect(model.totals.reviewerSplits).toBe(1)
    // Both claims were graded by both reviewers: 4 grades, 1 outside its majority.
    expect(task.changedGrades).toBe(1)
    expect(task.multiGradedGrades).toBe(4)
    expect(task.minorityGrades).toBe(1)
    expect(task.claims[1].minorityGrades).toBe(1)
    expect(task.claims[0].minorityGrades).toBe(0)
    expect(model.totals.minorityGrades).toBe(1)
  })

  test("flagged reviews and flagged claims are counted", () => {
    // Claim 1: Bo raised an issue. Claim 2: Ann changed the grade without a
    // flag, which is a grade disagreement, not a review flag.
    expect(task.claims[0].flaggedReviews).toBe(1)
    expect(task.claims[1].flaggedReviews).toBe(0)
    expect(task.claims[1].flagged).toBe(false)
    expect(task.flaggedClaims).toBe(1)
    expect(task.flaggedReviews).toBe(1)
    expect(model.totals.flaggedReviews).toBe(1)
    expect(model.totals.issues).toBe(1)
    expect(model.totals.flaggedClaims).toBe(1)
  })

  test("reviewer names come from full names, then the email", () => {
    expect(model.reviewerById["u-bo"].name).toBe("Bo Expert")
    expect(model.reviewerById["u-ann"].name).toBe("ann")
    expect(model.reviewerById["u-bo"].initials).toBe("BE")
    expect(model.reviewerById["u-bo"].kind).toBe("expert")
  })

  test("added claims count once per distinct claim, additions per reviewer", () => {
    // Ann and Bo both added "Gamma holds"; Bo made it by splitting claim 2.
    expect(task.addedClaims).toBe(1)
    expect(task.addedFromSplits).toBe(1)
    expect(task.additions).toBe(2)
    expect(model.totals.added).toBe(1)
    expect(model.totals.splits).toBe(1)
    expect(model.totals.additions).toBe(2)
  })

  test("added claims with the same text share one row, each reviewer keeping their grade", () => {
    const groups = groupAddedClaims(task.added)
    expect(groups).toHaveLength(1)
    expect(groups[0].reviewerIds).toEqual(["u-ann", "u-bo"])
    expect(groups[0].byReviewer["u-ann"].label).toBe("unimportant")
    expect(groups[0].byReviewer["u-bo"].label).toBe("semi-important")
    expect(groups[0].labelVotes).toEqual({
      vital: 0,
      "semi-important": 1,
      unimportant: 1,
    })
  })

  test("added claims take the positions after the model's, for coloring", () => {
    expect(addedPositionBase(task)).toBe(2)
    const owners = claimColorOwners(task)
    expect(owners.map((owner) => owner.position)).toEqual([0, 1, 2])
    expect(owners.map((owner) => owner.added)).toEqual([false, false, true])
    expect(owners[2].spans).toHaveLength(2)
  })

  test("an unreviewed task counts as unreviewed", () => {
    const quiet = buildGradesModel(
      [{ ...item, id: 2, case_id: "case-2", correction_reviews: [] }],
      users,
      {},
    )
    expect(quiet.tasks[0].reviewed).toBe(false)
    expect(quiet.totals.unreviewed).toBe(1)
    expect(quiet.totals.claims).toBe(2)
    expect(quiet.totals.judged).toBe(0)
  })
})

describe("normalizeClaimText", () => {
  test("ignores case, punctuation and spacing", () => {
    expect(normalizeClaimText("  Gamma   holds. ")).toBe(
      normalizeClaimText("gamma holds"),
    )
  })
})

describe("percent", () => {
  test("rounds, and shows a dash for an empty denominator", () => {
    expect(percent(4, 18)).toBe("22%")
    expect(percent(0, 0)).toBe("—")
  })
})
