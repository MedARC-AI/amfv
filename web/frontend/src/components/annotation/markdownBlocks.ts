export type SelectableBlock = {
  key: string
  srcStart: number
  text: string
}

type ListItem = { srcStart: number; text: string }
type TableCell = { srcStart: number; text: string }

export type MarkdownBlock =
  | { kind: "heading"; level: number; srcStart: number; text: string }
  | { kind: "list"; srcStart: number; items: ListItem[] }
  | { kind: "table"; srcStart: number; rows: TableCell[][] }
  | { kind: "paragraph"; srcStart: number; text: string }

/** Convert a Python code-point offset to the DOM's UTF-16 code-unit offset. */
export function codePointOffsetToCodeUnitOffset(
  text: string,
  codePointOffset: number,
): number {
  if (codePointOffset < 0) {
    return 0
  }
  return Array.from(text).slice(0, codePointOffset).join("").length
}

function splitTableRow(
  row: string,
  rowStart: number,
): Array<{ srcStart: number; text: string }> {
  const contentStart = row.startsWith("|") ? 1 : 0
  const contentEnd = row.endsWith("|") ? row.length - 1 : row.length
  const cells: Array<{ srcStart: number; text: string }> = []
  let cellStart = contentStart

  for (let index = contentStart; index < contentEnd; index += 1) {
    if (row[index] === "|" && row[index - 1] !== "\\") {
      cells.push(trimCell(row.slice(cellStart, index), rowStart + cellStart))
      cellStart = index + 1
    }
  }
  cells.push(trimCell(row.slice(cellStart, contentEnd), rowStart + cellStart))
  return cells
}

function trimCell(
  text: string,
  srcStart: number,
): { srcStart: number; text: string } {
  const leading = /^\s*/.exec(text)?.[0].length ?? 0
  return { srcStart: srcStart + leading, text: text.trim() }
}

function isSeparatorRow(row: Array<{ text: string }>): boolean {
  return row.length > 0 && row.every((cell) => /^:?-{3,}:?$/.test(cell.text))
}

function parseTable(source: string, srcStart: number): MarkdownBlock | null {
  const lines = source.split("\n")
  if (lines.length < 3) {
    return null
  }

  let lineStart = srcStart
  const rows = lines.map((line) => {
    const row = splitTableRow(line, lineStart)
    lineStart += line.length + 1
    return row
  })
  if (!isSeparatorRow(rows[1])) {
    return null
  }
  return { kind: "table", srcStart, rows: [rows[0], ...rows.slice(2)] }
}

/**
 * Parse stored chunk markdown without changing its source offsets. The returned
 * text always appears at the same UTF-16 offset from which the DOM selection is
 * measured; callers then convert that range to Python code points at the API
 * boundary.
 */
export function parseMarkdownBlocks(source: string): MarkdownBlock[] {
  const blocks: MarkdownBlock[] = []
  let offset = 0
  for (const part of source.split("\n\n")) {
    const start = offset
    offset += part.length + 2

    const heading = /^(#{1,6})\s+/.exec(part)
    if (heading) {
      const marker = heading[0]
      blocks.push({
        kind: "heading",
        level: heading[1].length,
        srcStart: start + marker.length,
        text: part.slice(marker.length),
      })
      continue
    }

    const table = parseTable(part, start)
    if (table) {
      blocks.push(table)
      continue
    }

    const bullet = /^[-*]\s+/.exec(part)
    if (bullet) {
      const item: ListItem = {
        srcStart: start + bullet[0].length,
        text: part.slice(bullet[0].length),
      }
      const previous = blocks[blocks.length - 1]
      if (previous?.kind === "list") {
        previous.items.push(item)
      } else {
        blocks.push({ kind: "list", srcStart: item.srcStart, items: [item] })
      }
      continue
    }

    if (part.length > 0) {
      blocks.push({ kind: "paragraph", srcStart: start, text: part })
    }
  }
  return blocks
}

export function flattenSelectableBlocks(
  blocks: MarkdownBlock[],
): SelectableBlock[] {
  const selectableBlocks: SelectableBlock[] = []
  for (const block of blocks) {
    if (block.kind === "list") {
      for (const item of block.items) {
        selectableBlocks.push({
          key: `list-${item.srcStart}`,
          srcStart: item.srcStart,
          text: item.text,
        })
      }
      continue
    }
    if (block.kind === "table") {
      block.rows.forEach((row, rowIndex) => {
        row.forEach((cell, cellIndex) => {
          selectableBlocks.push({
            key: `table-${rowIndex}-${cellIndex}-${cell.srcStart}`,
            srcStart: cell.srcStart,
            text: cell.text,
          })
        })
      })
      continue
    }
    selectableBlocks.push({
      key: `${block.kind}-${block.srcStart}`,
      srcStart: block.srcStart,
      text: block.text,
    })
  }
  return selectableBlocks
}
