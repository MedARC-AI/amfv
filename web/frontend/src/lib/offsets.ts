import type { EvidenceSpan } from "@/client"

export type CodeUnitRange = {
  start: number
  end: number
}

export type EvidenceSelection = EvidenceSpan & {
  text: string
}

export function codeUnitOffsetToCodePointOffset(
  text: string,
  codeUnitOffset: number,
): number {
  if (codeUnitOffset < 0 || codeUnitOffset > text.length) {
    throw new RangeError("Code-unit offset is outside the text bounds")
  }
  return Array.from(text.slice(0, codeUnitOffset)).length
}

export function sliceCodePoints(
  text: string,
  start: number,
  end: number,
): string {
  const points = Array.from(text)
  if (start < 0 || end > points.length || end <= start) {
    throw new RangeError("Code-point range is outside the text bounds")
  }
  return points.slice(start, end).join("")
}

export function codeUnitRangeToEvidenceSpan(
  chunkId: number,
  chunkText: string,
  range: CodeUnitRange,
): EvidenceSelection {
  const startUnit = Math.min(range.start, range.end)
  const endUnit = Math.max(range.start, range.end)
  const start = codeUnitOffsetToCodePointOffset(chunkText, startUnit)
  const end = codeUnitOffsetToCodePointOffset(chunkText, endUnit)
  const selectedText = sliceCodePoints(chunkText, start, end)
  return {
    chunk_id: chunkId,
    start,
    end,
    text: selectedText,
  }
}

export function evidenceSpanTextMatches(
  chunkText: string,
  span: EvidenceSpan,
): boolean {
  try {
    return sliceCodePoints(chunkText, span.start, span.end) === span.text
  } catch {
    return false
  }
}
