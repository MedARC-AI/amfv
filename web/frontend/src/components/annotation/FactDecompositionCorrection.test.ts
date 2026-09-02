import { describe, expect, test } from "bun:test"

import { CLAIM_LABELS } from "./ClaimCorrectionList"
import { responseSelectionText } from "./SelectableClaimResponse"

describe("fact decomposition correction contract", () => {
  test("exposes exactly the four shared labels", () => {
    expect(CLAIM_LABELS).toEqual([
      "vital",
      "supporting",
      "peripheral",
      "duplicate",
    ])
  })

  test("joins additive source selections in source order", () => {
    const response = "First claim. Omitted middle. Final claim."
    const finalStart = response.indexOf("Final")
    expect(
      responseSelectionText(response, [
        {
          start: finalStart,
          end: finalStart + "Final claim.".length,
          text: "Final claim.",
        },
        { start: 0, end: 5, text: "First" },
      ]),
    ).toBe("First\nFinal claim.")
  })
})
