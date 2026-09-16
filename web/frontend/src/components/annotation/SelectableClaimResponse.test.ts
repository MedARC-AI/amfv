import { describe, expect, test } from "bun:test"

import { normalizeResponseSpans } from "./SelectableClaimResponse"

describe("selectable claim response spans", () => {
  test("orders discontiguous spans and merges overlaps", () => {
    const response = "Alpha beta gamma"

    expect(
      normalizeResponseSpans(response, [
        { start: 11, end: 16, text: "gamma" },
        { start: 0, end: 5, text: "Alpha" },
        { start: 3, end: 10, text: "ha beta" },
      ]),
    ).toEqual([
      { start: 0, end: 10, text: "Alpha beta" },
      { start: 11, end: 16, text: "gamma" },
    ])
  })
})
