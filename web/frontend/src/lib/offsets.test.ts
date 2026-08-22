import { describe, expect, test } from "bun:test"

import {
  codeUnitOffsetToCodePointOffset,
  codeUnitRangeToEvidenceSpan,
  evidenceSpanTextMatches,
  sliceCodePoints,
} from "./offsets"

describe("code-point offsets", () => {
  test("converts UTF-16 code-unit offsets across emoji", () => {
    const text = "A😀B"

    expect(text.length).toBe(4)
    expect(Array.from(text)).toHaveLength(3)
    expect(codeUnitOffsetToCodePointOffset(text, 3)).toBe(2)
    expect(sliceCodePoints(text, 1, 2)).toBe("😀")
  })

  test("keeps combining marks unnormalized", () => {
    const text = "Cafe\u0301 au lait"
    const selected = "e\u0301"
    const startUnit = text.indexOf(selected)
    const endUnit = startUnit + selected.length

    const span = codeUnitRangeToEvidenceSpan(10, text, {
      start: startUnit,
      end: endUnit,
    })

    expect(span).toEqual({
      chunk_id: 10,
      start: 3,
      end: 5,
      text: selected,
    })
    expect(span.text).not.toBe("é")
  })

  test("normalizes reversed browser selections", () => {
    const text = "Alpha Baker Gamma"
    const start = text.indexOf("Baker")
    const end = start + "Baker".length

    expect(
      codeUnitRangeToEvidenceSpan(7, text, { start: end, end: start }),
    ).toEqual({
      chunk_id: 7,
      start,
      end,
      text: "Baker",
    })
  })

  test("detects stale or invalid evidence spans", () => {
    const text = "Fresh evidence"

    expect(
      evidenceSpanTextMatches(text, {
        chunk_id: 1,
        start: 0,
        end: 5,
        text: "Fresh",
      }),
    ).toBe(true)
    expect(
      evidenceSpanTextMatches(text, {
        chunk_id: 1,
        start: 0,
        end: 5,
        text: "Stale",
      }),
    ).toBe(false)
    expect(
      evidenceSpanTextMatches(text, {
        chunk_id: 1,
        start: 0,
        end: 100,
        text: "Fresh",
      }),
    ).toBe(false)
  })
})
