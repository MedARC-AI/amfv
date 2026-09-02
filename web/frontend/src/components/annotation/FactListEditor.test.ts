import { describe, expect, test } from "bun:test"

import type { FactDraft } from "@/client"

import {
  selectedIndexAfterMove,
  selectedIndexAfterRemove,
} from "./FactListEditor"

describe("fact selection transitions", () => {
  test("keeps the selected fact attached when a preceding row moves", () => {
    // [source fact, selected fact, third fact] -> [selected fact, source fact, third fact]
    const facts: FactDraft[] = [
      {
        fact_text: "source",
        polarity: "SHOULD_LIST",
        provenance_spans: [{ chunk_id: 1, start: 0, end: 6, text: "source" }],
      },
      {
        fact_text: "selected",
        polarity: "SHOULD_LIST",
        provenance_spans: [
          { chunk_id: 1, start: 7, end: 15, text: "selected" },
        ],
      },
    ]
    const nextFacts = [...facts]
    const [moved] = nextFacts.splice(0, 1)
    nextFacts.splice(1, 0, moved)
    const selectedIndex = selectedIndexAfterMove(1, 0, 1)

    expect(selectedIndex).toBe(0)
    expect(
      selectedIndex === null
        ? null
        : nextFacts[selectedIndex]?.provenance_spans?.[0]?.text,
    ).toBe("selected")
  })

  test("shifts the selected fact when a preceding row is removed", () => {
    // The selected fact's provenance moves from position 2 to position 1.
    const facts: FactDraft[] = [
      {
        fact_text: "first",
        polarity: "SHOULD_LIST",
        provenance_spans: [{ chunk_id: 1, start: 0, end: 5, text: "first" }],
      },
      {
        fact_text: "preceding",
        polarity: "SHOULD_LIST",
        provenance_spans: [
          { chunk_id: 1, start: 6, end: 15, text: "preceding" },
        ],
      },
      {
        fact_text: "selected",
        polarity: "SHOULD_LIST",
        provenance_spans: [
          { chunk_id: 1, start: 16, end: 24, text: "selected" },
        ],
      },
    ]
    const nextFacts = facts.filter((_fact, index) => index !== 0)
    const selectedIndex = selectedIndexAfterRemove(2, 0, nextFacts.length)

    expect(selectedIndex).toBe(1)
    expect(
      selectedIndex === null ? null : nextFacts[selectedIndex]?.fact_text,
    ).toBe("selected")
    expect(
      selectedIndex === null
        ? null
        : nextFacts[selectedIndex]?.provenance_spans?.[0]?.text,
    ).toBe("selected")
  })

  test("selects the next row when the selected fact is removed", () => {
    expect(selectedIndexAfterRemove(1, 1, 2)).toBe(1)
    expect(selectedIndexAfterRemove(1, 1, 0)).toBeNull()
  })
})
