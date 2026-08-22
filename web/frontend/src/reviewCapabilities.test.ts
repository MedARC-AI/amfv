import { describe, expect, test } from "bun:test"

import type {
  FactDecompReviewPayload,
  RelevanceReviewPayload,
  RetrievalReviewPayload,
} from "@/client"
import {
  isReviewActionAllowed,
  retrievalReviewAction,
} from "./reviewCapabilities"

describe("server review capabilities", () => {
  test("does not authorize an unavailable retrieval command", () => {
    const allowedActions: RetrievalReviewPayload["allowed_actions"] = ["accept"]

    expect(retrievalReviewAction(false)).toBe("reject")
    expect(isReviewActionAllowed(allowedActions, "reject")).toBe(false)
  })

  test("requires the workflow-specific literal command", () => {
    const factActions: FactDecompReviewPayload["allowed_actions"] = [
      "save_review",
    ]
    const relevanceActions: RelevanceReviewPayload["allowed_actions"] = [
      "grade_relevance",
    ]

    expect(isReviewActionAllowed(factActions, "save_review")).toBe(true)
    expect(isReviewActionAllowed(relevanceActions, "grade_relevance")).toBe(
      true,
    )
    expect(retrievalReviewAction(null)).toBeNull()
  })
})
