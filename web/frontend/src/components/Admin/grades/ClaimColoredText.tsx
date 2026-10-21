import { type MouseEvent, type ReactNode, useState } from "react"

import type { ResponseClaimSpan } from "@/client"
import { evalItemColor } from "@/lib/evalItemPalette"
import { cn } from "@/lib/utils"

type ClaimColorOwner = {
  /** The claim's position, which also picks its palette color. */
  position: number
  spans: ResponseClaimSpan[]
  /** A claim a reviewer added; its line goes above the text. */
  added?: boolean
}

/**
 * Lines are borders on nested inline spans, not text decorations, so they run
 * unbroken under spaces. Each depth pads a little more to sit lower.
 */
const UNDERLINE_OFFSETS = ["pb-[2px]", "pb-[6px]", "pb-[10px]"]

/**
 * Which claim's line the pointer is on: the top line of an added claim when
 * the pointer sits on it, or a model claim's underline when the pointer is
 * in the padding below the words. Over the words themselves, null.
 */
function lineUnderPointer(event: MouseEvent): number | null {
  const target = event.target as HTMLElement
  const line = target.closest<HTMLElement>("[data-line-position]")
  if (!line) return null
  const position = Number(line.dataset.linePosition)
  if (line.dataset.lineSide === "top") {
    // The overline span also wraps the words; only its top edge counts.
    for (const rect of line.getClientRects()) {
      if (event.clientX >= rect.left && event.clientX <= rect.right) {
        return event.clientY <= rect.top + 4 ? position : null
      }
    }
    return null
  }
  return position
}

/**
 * The source text with each claim's passage marked in the claim's color: a
 * line under the words for each model claim (stacked when two share the same
 * words), a line above them for a claim a reviewer added. Spans are
 * code-point offsets, so the text is split with Array.from like the review
 * page does.
 */
export default function ClaimColoredText({
  text,
  owners,
  activePosition = null,
  onSelect,
  className,
}: {
  text: string
  owners: ClaimColorOwner[]
  activePosition?: number | null
  onSelect?: (position: number) => void
  className?: string
}) {
  // The claim whose passage is under the pointer; its whole passage lights up.
  const [hovered, setHovered] = useState<number | null>(null)
  const labelFor = (owner: ClaimColorOwner) => {
    if (!owner.added) return `Claim ${owner.position + 1}`
    const addedIndex = owners
      .filter((candidate) => candidate.added)
      .findIndex((candidate) => candidate.position === owner.position)
    return `Added claim +${addedIndex + 1}`
  }
  const points = Array.from(text)
  const boundaries = new Set<number>([0, points.length])
  for (const owner of owners) {
    for (const span of owner.spans) {
      boundaries.add(Math.min(points.length, Math.max(0, span.start)))
      boundaries.add(Math.min(points.length, Math.max(0, span.end)))
    }
  }
  const sorted = [...boundaries].sort((a, b) => a - b)
  // Each model claim's span keeps one underline depth along its whole length:
  // the shallowest depth no earlier overlapping model claim already uses.
  const depthOf = new Map<string, number>()
  const placed: { start: number; end: number; depth: number }[] = []
  for (const owner of owners) {
    if (owner.added) continue
    for (const span of owner.spans) {
      const taken = new Set(
        placed
          .filter((other) => other.start < span.end && span.start < other.end)
          .map((other) => other.depth),
      )
      let depth = 0
      while (taken.has(depth)) depth += 1
      depthOf.set(`${owner.position}:${span.start}:${span.end}`, depth)
      placed.push({ start: span.start, end: span.end, depth })
    }
  }
  return (
    <span className={cn("whitespace-pre-line", className)}>
      {sorted.slice(0, -1).map((start, i) => {
        const end = sorted[i + 1]
        if (start >= end) return null
        const covering = owners.filter((candidate) =>
          candidate.spans.some(
            (span) => span.start <= start && span.end >= end,
          ),
        )
        const piece = points.slice(start, end).join("")
        if (covering.length === 0) return <span key={start}>{piece}</span>
        // Model claims stack their lines under the words at their fixed
        // depths; a claim a reviewer added puts its line above.
        const belows = covering
          .filter((owner) => !owner.added)
          .map((owner) => {
            const span = owner.spans.find(
              (candidate) => candidate.start <= start && candidate.end >= end,
            )
            const depth = span
              ? (depthOf.get(`${owner.position}:${span.start}:${span.end}`) ??
                0)
              : 0
            return {
              owner,
              depth: Math.min(depth, UNDERLINE_OFFSETS.length - 1),
            }
          })
          .sort((a, b) => a.depth - b.depth)
        const above = covering.find((owner) => owner.added) ?? null
        const current = covering.findIndex(
          (owner) => owner.position === activePosition,
        )
        const next = covering[(current + 1) % covering.length]
        const selectNext = () => {
          if (onSelect) onSelect(next.position)
        }
        let marked: ReactNode = above ? (
          <span
            className={cn(
              "border-t-2 pt-[2px]",
              (hovered === above.position ||
                activePosition === above.position) &&
                "border-dashed",
              evalItemColor(above.position).line,
            )}
            data-line-position={above.position}
            data-line-side="top"
          >
            {piece}
          </span>
        ) : (
          piece
        )
        for (const { owner, depth } of belows) {
          marked = (
            <span
              className={cn(
                "border-b-2",
                (hovered === owner.position ||
                  activePosition === owner.position) &&
                  "border-dashed",
                UNDERLINE_OFFSETS[depth],
                evalItemColor(owner.position).line,
              )}
              data-line-position={owner.position}
            >
              {marked}
            </span>
          )
        }
        return (
          // biome-ignore lint/a11y/noStaticElementInteractions: an inline span keeps the text flowing as prose; a button would not.
          <span
            className={cn(
              "rounded-sm transition-colors",
              onSelect && "cursor-pointer",
            )}
            key={start}
            onClick={
              onSelect
                ? (event) => onSelect(lineUnderPointer(event) ?? next.position)
                : undefined
            }
            onMouseLeave={onSelect ? () => setHovered(null) : undefined}
            onMouseMove={
              onSelect
                ? (event) =>
                    setHovered(lineUnderPointer(event) ?? next.position)
                : undefined
            }
            title={
              onSelect
                ? `${covering.map(labelFor).join(" · ")} · click to select ${labelFor(next)}`
                : undefined
            }
            onKeyDown={
              onSelect
                ? (event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault()
                      selectNext()
                    }
                  }
                : undefined
            }
            role={onSelect ? "button" : undefined}
            tabIndex={onSelect ? 0 : undefined}
          >
            {marked}
          </span>
        )
      })}
    </span>
  )
}
