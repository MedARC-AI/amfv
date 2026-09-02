import { describe, expect, test } from "bun:test"

import {
  normalizeResponseSpans,
  responseSpanFromCodeUnitRange,
} from "./SelectableClaimResponse"

describe("selectable claim response spans", () => {
  test("keeps code-point offsets after an emoji before the selection", () => {
    const response = "Reasoning 😀 then the omitted claim"
    const start = response.indexOf("the omitted")
    const end = start + "the omitted claim".length

    expect(responseSpanFromCodeUnitRange(response, start, end)).toEqual({
      start: Array.from(response.slice(0, start)).length,
      end: Array.from(response.slice(0, end)).length,
      text: "the omitted claim",
    })
  })

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
