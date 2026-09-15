import { describe, expect, test } from "bun:test"

import { responseSelectionText } from "./SelectableClaimResponse"

describe("fact decomposition correction contract", () => {
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
