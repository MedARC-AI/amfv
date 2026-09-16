import * as React from "react"

import { cn } from "@/lib/utils"

export type ClaimResponseSpan = {
  start: number
  end: number
  text: string
}

export type ClaimResponseOwner = {
  position: number
  spans: ClaimResponseSpan[]
  markClass: string
  dotClass: string
}

type SelectableClaimResponseProps = {
  response: string
  owners: ClaimResponseOwner[]
  stagedSpans?: ClaimResponseSpan[]
  activePosition?: number | null
  onHighlightClick?: (position: number) => void
  onSelectionChange?: (spans: ClaimResponseSpan[], additive: boolean) => void
  className?: string
}

type Boundary = { node: Node; offset: number }

function sourceOffset(boundary: Boundary): number | null {
  const element =
    boundary.node.nodeType === Node.TEXT_NODE
      ? boundary.node.parentElement
      : boundary.node instanceof Element
        ? boundary.node
        : null
  const source = element?.closest<HTMLElement>("[data-source-start]")
  if (!source) return null
  const start = Number(source.dataset.sourceStart)
  if (!Number.isFinite(start)) return null
  if (boundary.node.nodeType !== Node.TEXT_NODE) return start
  const range = document.createRange()
  range.selectNodeContents(source)
  range.setEnd(boundary.node, boundary.offset)
  // data-source-start is a code-point offset. DOM ranges report UTF-16
  // lengths, so convert only the local segment before adding it.
  const localCodePoints = Array.from(range.toString()).length
  return start + localCodePoints
}

function selectionSpan(response: string): ClaimResponseSpan | null {
  const selection = window.getSelection()
  if (!selection || selection.rangeCount === 0 || selection.isCollapsed)
    return null
  const range = selection.getRangeAt(0)
  const start = sourceOffset({
    node: range.startContainer,
    offset: range.startOffset,
  })
  const end = sourceOffset({
    node: range.endContainer,
    offset: range.endOffset,
  })
  if (start === null || end === null || start === end) return null
  return responseSpanFromCodePointRange(response, start, end)
}

function responseSpanFromCodePointRange(
  response: string,
  start: number,
  end: number,
): ClaimResponseSpan | null {
  const text = Array.from(response).slice(start, end).join("")
  return text ? { start, end, text } : null
}

export function normalizeResponseSpans(
  response: string,
  spans: ClaimResponseSpan[],
): ClaimResponseSpan[] {
  const ordered = spans
    .filter((span) => span.end > span.start && span.text)
    .sort((a, b) => a.start - b.start || a.end - b.end)
    .filter(
      (span, index, all) =>
        index === 0 ||
        span.start !== all[index - 1].start ||
        span.end !== all[index - 1].end,
    )
  const normalized: ClaimResponseSpan[] = []
  for (const span of ordered) {
    const previous = normalized[normalized.length - 1]
    if (previous && span.start < previous.end) {
      previous.end = Math.max(previous.end, span.end)
      previous.text = Array.from(response)
        .slice(previous.start, previous.end)
        .join("")
    } else {
      normalized.push({ ...span })
    }
  }
  return normalized
}

/**
 * Renders a response as one source-coordinate surface. Overlapping claims are
 * represented by one readable fill and owner-color edge markers, so React
 * never nests marks or changes the offsets used by selection.
 */
export function SelectableClaimResponse({
  activePosition = null,
  className,
  onHighlightClick,
  onSelectionChange,
  owners,
  response,
  stagedSpans = [],
}: SelectableClaimResponseProps) {
  const [overlapOwner, setOverlapOwner] = React.useState<
    Record<string, number>
  >({})
  const points = Array.from(response)
  const boundaries = new Set<number>([0, points.length])
  for (const owner of owners) {
    for (const span of owner.spans) {
      boundaries.add(Math.max(0, span.start))
      boundaries.add(Math.min(points.length, span.end))
    }
  }
  for (const span of stagedSpans) {
    boundaries.add(Math.max(0, span.start))
    boundaries.add(Math.min(points.length, span.end))
  }
  const sorted = [...boundaries].sort((a, b) => a - b)

  const segmentOwners = (start: number, end: number) =>
    owners.filter((owner) =>
      owner.spans.some((span) => span.start <= start && span.end >= end),
    )

  const selectSegmentOwner = (
    start: number,
    end: number,
    segmentOwnersList: ClaimResponseOwner[],
  ) => {
    if (!onHighlightClick || segmentOwnersList.length === 0) return
    const key = `${start}:${end}`
    const current =
      overlapOwner[key] ??
      (activePosition === null
        ? -1
        : segmentOwnersList.findIndex(
            (owner) => owner.position === activePosition,
          ))
    const next = segmentOwnersList[(current + 1) % segmentOwnersList.length]
    setOverlapOwner((previous) => ({
      ...previous,
      [key]: (current + 1) % segmentOwnersList.length,
    }))
    onHighlightClick(next.position)
  }

  const handleSelection = (event: React.MouseEvent | React.KeyboardEvent) => {
    const span = selectionSpan(response)
    if (!span || !onSelectionChange) return
    onSelectionChange([span], event.metaKey || event.ctrlKey)
    window.getSelection()?.removeAllRanges()
  }

  return (
    <section
      aria-label="Assistant response; select text to create a human claim"
      className={cn(
        "relative select-text whitespace-pre-wrap break-words rounded-lg border bg-background p-4 font-mono text-sm leading-7",
        className,
      )}
      data-testid="claim-response"
      onMouseUp={handleSelection}
    >
      {sorted.slice(0, -1).map((start, index) => {
        const end = sorted[index + 1]
        if (start >= end) return null
        const highlighted = segmentOwners(start, end)
        const staged = stagedSpans.some(
          (span) => span.start <= start && span.end >= end,
        )
        const selectedOwner =
          highlighted.find((owner) => owner.position === activePosition) ??
          highlighted[0]
        const ownerKey = `${start}:${end}`
        return (
          // biome-ignore lint/a11y/noStaticElementInteractions: An inline span preserves exact selectable source text and cannot be replaced by a button.
          <span
            className={cn(
              "relative rounded-sm px-0.5 transition-colors",
              selectedOwner?.markClass ??
                (highlighted.length ? "bg-primary/15" : undefined),
              staged && "bg-primary/25 ring-1 ring-primary/50",
              highlighted.length && "cursor-pointer",
            )}
            data-owner-positions={
              highlighted.map((owner) => owner.position).join(",") || undefined
            }
            data-source-start={start}
            key={ownerKey}
            onClick={() => selectSegmentOwner(start, end, highlighted)}
            onKeyDown={(event) => {
              if (
                highlighted.length &&
                (event.key === "Enter" || event.key === " ")
              ) {
                event.preventDefault()
                selectSegmentOwner(start, end, highlighted)
              }
            }}
            role={highlighted.length ? "button" : undefined}
            tabIndex={highlighted.length ? 0 : undefined}
          >
            {highlighted.length > 1 ? (
              <span
                aria-hidden="true"
                className="pointer-events-none absolute inset-x-0 -top-0.5 flex h-0.5 gap-px opacity-80"
              >
                {highlighted.map((owner) => (
                  <span
                    className={cn("min-w-0 flex-1", owner.dotClass)}
                    key={owner.position}
                  />
                ))}
              </span>
            ) : null}
            {points.slice(start, end).join("")}
          </span>
        )
      })}
    </section>
  )
}

export function responseSelectionText(
  response: string,
  spans: ClaimResponseSpan[],
): string {
  return normalizeResponseSpans(response, spans)
    .map((span) => span.text)
    .join("\n")
}
