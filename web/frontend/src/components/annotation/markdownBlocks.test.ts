import { describe, expect, test } from "bun:test"

import {
  codePointOffsetToCodeUnitOffset,
  flattenSelectableBlocks,
  parseMarkdownBlocks,
} from "./markdownBlocks"

describe("markdown evidence offsets", () => {
  test("keeps markdown source offsets when headings, lists, and tables contain Unicode", () => {
    const source =
      "# Intro 😀\n\n- Cafe\u0301\n\n* Baker\n\n| Name | Value |\n| --- | --- |\n| 😀 | yes |"

    const blocks = parseMarkdownBlocks(source)
    expect(blocks).toMatchObject([
      { kind: "heading", srcStart: 2, text: "Intro 😀" },
      { kind: "list", items: [{ text: "Cafe\u0301" }, { text: "Baker" }] },
      { kind: "table" },
    ])

    const selectable = flattenSelectableBlocks(blocks)
    for (const block of selectable) {
      expect(
        source.slice(block.srcStart, block.srcStart + block.text.length),
      ).toBe(block.text)
    }
    expect(selectable.map((block) => block.text)).toEqual([
      "Intro 😀",
      "Cafe\u0301",
      "Baker",
      "Name",
      "Value",
      "😀",
      "yes",
    ])
  })

  test("translates code-point spans back to the DOM's UTF-16 source offsets", () => {
    const source = "A😀B Cafe\u0301"

    expect(codePointOffsetToCodeUnitOffset(source, 2)).toBe(3)
    expect(codePointOffsetToCodeUnitOffset(source, 6)).toBe(7)
  })

  test("retains the final source offset in long documents", () => {
    const source = Array.from(
      { length: 600 },
      (_, index) => `Paragraph ${index} 😀`,
    ).join("\n\n")
    const blocks = parseMarkdownBlocks(source)
    const finalBlock = blocks[blocks.length - 1]

    expect(blocks).toHaveLength(600)
    expect(finalBlock).toMatchObject({
      kind: "paragraph",
      srcStart: source.lastIndexOf("Paragraph 599 😀"),
      text: "Paragraph 599 😀",
    })
  })
})
