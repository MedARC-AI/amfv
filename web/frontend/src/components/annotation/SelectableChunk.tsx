import { Check } from "lucide-react"
import * as React from "react"

import type { ChunkSummary, EvidenceSpan } from "@/client"
import type { DocumentSearchMatch } from "@/lib/documentSearch"
import { codeUnitRangeToEvidenceSpan } from "@/lib/offsets"
import { cn } from "@/lib/utils"
import {
  DocumentSelectionController,
  isSearchSelectionGesture,
  useDocumentSelectionController,
} from "./documentSelectionController"
import {
  codePointOffsetToCodeUnitOffset,
  flattenSelectableBlocks,
  parseMarkdownBlocks,
} from "./markdownBlocks"

type SelectedEvidenceSpan = EvidenceSpan & {
  id?: string
  /** Added eval item this committed span belongs to (enables click-to-scroll). */
  itemId?: string
  /** Per-item color classes for an inline (exact) committed highlight. */
  markClass?: string
  /** Per-item color classes for a block committed highlight. */
  blockClass?: string
}

type SelectableChunkProps = {
  chunk: ChunkSummary
  committedSpans?: SelectedEvidenceSpan[]
  selectedSpans?: SelectedEvidenceSpan[]
  onSelect: (span: EvidenceSpan) => boolean | undefined
  onBlockSelect?: (selection: BlockSelection) => boolean | undefined
  onConfirmSelection?: () => void
  onRemove?: (span: SelectedEvidenceSpan) => void
  onCommittedSelect?: (itemId: string) => void
  onSearchSelect?: (text: string) => void
  searchMatches?: DocumentSearchMatch[]
  activeSearchMatchIndex?: number
  selectionMode?: "exact" | "block"
  className?: string
  readOnly?: boolean
}

export type BlockSelection = {
  additive: boolean
  range: boolean
  rangeSpans: EvidenceSpan[]
  span: EvidenceSpan
}

type SelectableTextProps = {
  chunk: ChunkSummary
  committedSpans: SelectedEvidenceSpan[]
  onCommittedSelect?: (itemId: string) => void
  onRemove?: (span: SelectedEvidenceSpan) => void
  searchMatches: DocumentSearchMatch[]
  activeSearchMatchIndex?: number
  selectedSpans: SelectedEvidenceSpan[]
  srcStart: number
  text: string
}

type SelectionBoundary = {
  node: Node
  offset: number
}

type SelectionOverlayRect = {
  height: number
  left: number
  top: number
  width: number
}

const EMPTY_SELECTED_EVIDENCE_SPANS: SelectedEvidenceSpan[] = []
const EMPTY_SEARCH_MATCHES: DocumentSearchMatch[] = []

function evidenceAnchorId(chunkId: number, start: number, end: number): string {
  return `evidence-${chunkId}-${start}-${end}`
}

function textSourceSpan(srcStart: number, text: string, key: React.Key) {
  return (
    <span data-evidence-src-start={srcStart} key={key}>
      {text}
    </span>
  )
}

function sourceOffsetFromBoundary(boundary: SelectionBoundary): number | null {
  const parent =
    boundary.node.nodeType === Node.TEXT_NODE
      ? boundary.node.parentElement
      : boundary.node instanceof Element
        ? boundary.node
        : null
  const sourceElement = parent?.closest<HTMLElement>(
    "[data-evidence-src-start]",
  )
  if (!sourceElement) {
    return null
  }
  const srcStart = Number(sourceElement.dataset.evidenceSrcStart)
  if (!Number.isFinite(srcStart)) {
    return null
  }
  if (boundary.node.nodeType !== Node.TEXT_NODE) {
    return srcStart
  }

  const beforeBoundary = document.createRange()
  beforeBoundary.selectNodeContents(sourceElement)
  beforeBoundary.setEnd(boundary.node, boundary.offset)
  return srcStart + beforeBoundary.toString().length
}

const headingClass: Record<number, string> = {
  1: "text-2xl font-semibold",
  2: "text-xl font-semibold",
  3: "text-lg font-semibold",
  4: "text-base font-semibold",
  5: "text-base font-medium",
  6: "text-base font-medium text-muted-foreground",
}

function SelectableText({
  activeSearchMatchIndex,
  chunk,
  committedSpans,
  onCommittedSelect,
  onRemove,
  searchMatches,
  selectedSpans,
  srcStart,
  text,
}: SelectableTextProps) {
  const evidenceHighlights = [
    ...selectedSpans.map((span) => ({ span, committed: false })),
    ...committedSpans.map((span) => ({ span, committed: true })),
  ]
    .map((span) => {
      const start = codePointOffsetToCodeUnitOffset(chunk.text, span.span.start)
      const end = codePointOffsetToCodeUnitOffset(chunk.text, span.span.end)
      return {
        start: Math.max(start - srcStart, 0),
        end: Math.min(end - srcStart, text.length),
        sourceStart: start,
        span: span.span,
        committed: span.committed,
      }
    })
    .filter((span) => span.start < span.end)
    .sort((a, b) => a.start - b.start)
  const searchHighlights = searchMatches
    .map((match) => ({
      start: Math.max(match.start - srcStart, 0),
      end: Math.min(match.end - srcStart, text.length),
      sourceStart: match.start,
      match,
      active: match.index === activeSearchMatchIndex,
    }))
    .filter((match) => match.start < match.end)

  if (evidenceHighlights.length === 0 && searchHighlights.length === 0) {
    return textSourceSpan(srcStart, text, "text")
  }

  const boundaries = new Set([0, text.length])
  for (const highlight of evidenceHighlights) {
    boundaries.add(highlight.start)
    boundaries.add(highlight.end)
  }
  for (const highlight of searchHighlights) {
    boundaries.add(highlight.start)
    boundaries.add(highlight.end)
  }
  const sortedBoundaries = [...boundaries].sort((a, b) => a - b)
  const nodes: React.ReactNode[] = []
  sortedBoundaries.slice(0, -1).forEach((start, index) => {
    const end = sortedBoundaries[index + 1]
    if (start >= end) {
      return
    }
    const evidenceHighlight = evidenceHighlights.find(
      (highlight) => highlight.start <= start && highlight.end >= end,
    )
    const searchHighlight =
      searchHighlights.find(
        (highlight) =>
          highlight.active && highlight.start <= start && highlight.end >= end,
      ) ??
      searchHighlights.find(
        (highlight) => highlight.start <= start && highlight.end >= end,
      )
    if (!evidenceHighlight && !searchHighlight) {
      nodes.push(
        textSourceSpan(srcStart + start, text.slice(start, end), start),
      )
      return
    }

    const committedItemId = evidenceHighlight?.committed
      ? evidenceHighlight.span.itemId
      : undefined
    const scrollToItem =
      committedItemId && onCommittedSelect
        ? () => onCommittedSelect(committedItemId)
        : undefined
    const showRemove =
      evidenceHighlight &&
      onRemove &&
      !evidenceHighlight.committed &&
      start === evidenceHighlight.start &&
      evidenceHighlight.sourceStart >= srcStart &&
      evidenceHighlight.sourceStart < srcStart + text.length
    nodes.push(
      <mark
        className={cn(
          "inline-flex items-baseline gap-1 rounded-sm px-0.5 text-foreground ring-1",
          searchHighlight?.active
            ? "bg-amber-300/90 ring-amber-600"
            : searchHighlight
              ? "bg-amber-200/80 ring-amber-300"
              : evidenceHighlight?.committed
                ? (evidenceHighlight.span.markClass ??
                  "bg-primary/10 ring-primary/15")
                : "bg-primary/20 ring-primary/30",
          evidenceHighlight &&
            searchHighlight?.active &&
            "outline outline-2 outline-offset-1 outline-primary/60",
          evidenceHighlight &&
            searchHighlight &&
            !searchHighlight.active &&
            "outline outline-1 outline-offset-1 outline-primary/40",
          scrollToItem && "cursor-pointer hover:brightness-95",
        )}
        data-evidence-src-start={srcStart + start}
        data-search-match-index={searchHighlight?.match.index}
        data-search-active={searchHighlight?.active ? "true" : undefined}
        id={
          evidenceHighlight
            ? evidenceAnchorId(
                chunk.id,
                evidenceHighlight.span.start,
                evidenceHighlight.span.end,
              )
            : undefined
        }
        key={`${start}-${end}-${index}`}
        onClick={scrollToItem}
        onKeyDown={
          scrollToItem
            ? (event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault()
                  scrollToItem()
                }
              }
            : undefined
        }
        role={scrollToItem ? "button" : undefined}
        tabIndex={scrollToItem ? 0 : undefined}
      >
        {text.slice(start, end)}
        {showRemove ? (
          <button
            aria-label="Remove selected evidence"
            className="rounded-sm px-1 text-xs font-semibold leading-none text-primary hover:bg-primary/15"
            onMouseDown={(event) => {
              event.preventDefault()
              event.stopPropagation()
            }}
            onMouseUp={(event) => {
              event.preventDefault()
              event.stopPropagation()
            }}
            onClick={(event) => {
              event.preventDefault()
              event.stopPropagation()
              onRemove(evidenceHighlight.span)
            }}
            onPointerDown={(event) => {
              event.preventDefault()
              event.stopPropagation()
            }}
            onPointerUp={(event) => {
              event.preventDefault()
              event.stopPropagation()
            }}
            type="button"
          >
            x
          </button>
        ) : null}
      </mark>,
    )
  })
  return nodes
}

function SelectableChunkView({
  activeSearchMatchIndex,
  chunk,
  committedSpans = [],
  onBlockSelect,
  onConfirmSelection,
  onCommittedSelect,
  onRemove,
  onSelect,
  onSearchSelect,
  readOnly = false,
  searchMatches = EMPTY_SEARCH_MATCHES,
  selectedSpans = [],
  selectionMode = "exact",
  className,
}: SelectableChunkProps) {
  const blocks = React.useMemo(
    () => parseMarkdownBlocks(chunk.text),
    [chunk.text],
  )
  const selectableBlocks = React.useMemo(
    () => flattenSelectableBlocks(blocks),
    [blocks],
  )
  const inlineCommittedSpans =
    selectionMode === "exact" ? committedSpans : EMPTY_SELECTED_EVIDENCE_SPANS
  const inlineSelectedSpans =
    selectionMode === "exact" ? selectedSpans : EMPTY_SELECTED_EVIDENCE_SPANS
  const rootRef = React.useRef<HTMLFieldSetElement>(null)
  const blockRefs = React.useRef<Map<number, HTMLElement>>(new Map())
  const [selectionOverlayRect, setSelectionOverlayRect] =
    React.useState<SelectionOverlayRect | null>(null)
  const [searchSelectionActive, setSearchSelectionActive] =
    React.useState(false)
  const inheritedSelectionController = useDocumentSelectionController()
  const localSelectionControllerRef = React.useRef<DocumentSelectionController>(
    new DocumentSelectionController(),
  )
  const selectionController =
    inheritedSelectionController ?? localSelectionControllerRef.current

  const setBlockRef = React.useCallback(
    (srcStart: number) => (element: HTMLElement | null) => {
      if (element) {
        blockRefs.current.set(srcStart, element)
      } else {
        blockRefs.current.delete(srcStart)
      }
    },
    [],
  )

  const commitSearchSelection = React.useCallback(
    (event: React.SyntheticEvent<HTMLElement>) => {
      setSearchSelectionActive(false)
      if (!onSearchSelect || !isSearchSelectionGesture(event.nativeEvent)) {
        return false
      }
      const root = event.currentTarget
      const selection = window.getSelection()
      if (!selection || selection.isCollapsed || selection.rangeCount === 0) {
        return false
      }
      const range = selection.getRangeAt(0)
      if (
        !root.contains(range.startContainer) ||
        !root.contains(range.endContainer)
      ) {
        return false
      }
      const searchText = range.toString().trim()
      if (searchText.length === 0) {
        return false
      }
      onSearchSelect(searchText)
      selection.removeAllRanges()
      return true
    },
    [onSearchSelect],
  )

  const startSearchSelection = React.useCallback(
    (event: React.PointerEvent<HTMLElement>) => {
      if (event.altKey && onSearchSelect) {
        setSearchSelectionActive(true)
      }
    },
    [onSearchSelect],
  )

  const commitExactSelection = React.useCallback(
    (event: React.SyntheticEvent<HTMLElement>) => {
      if (isSearchSelectionGesture(event.nativeEvent)) {
        return
      }
      if (readOnly) {
        return
      }
      if (selectionMode !== "exact") {
        return
      }
      const root = event.currentTarget
      const selection = window.getSelection()
      if (!selection || selection.isCollapsed || selection.rangeCount === 0) {
        return
      }
      const range = selection.getRangeAt(0)
      if (
        !root.contains(range.startContainer) ||
        !root.contains(range.endContainer)
      ) {
        return
      }

      const start = sourceOffsetFromBoundary({
        node: range.startContainer,
        offset: range.startOffset,
      })
      const end = sourceOffsetFromBoundary({
        node: range.endContainer,
        offset: range.endOffset,
      })
      if (start === null || end === null || start === end) {
        return
      }
      const span = codeUnitRangeToEvidenceSpan(chunk.id, chunk.text, {
        start: Math.min(start, end),
        end: Math.max(start, end),
      })
      if (onSelect(span) !== false) {
        selection.removeAllRanges()
      }
    },
    [chunk.id, chunk.text, onSelect, readOnly, selectionMode],
  )

  const commitSearchOrExactSelection = React.useCallback(
    (event: React.SyntheticEvent<HTMLElement>) => {
      if (commitSearchSelection(event)) {
        return
      }
      commitExactSelection(event)
    },
    [commitExactSelection, commitSearchSelection],
  )

  const commit = React.useCallback(
    (srcStart: number) => (event: React.SyntheticEvent<HTMLElement>) => {
      if (isSearchSelectionGesture(event.nativeEvent)) {
        return
      }
      if (readOnly) {
        return
      }
      const root = event.currentTarget
      const selection = window.getSelection()
      if (!selection || selection.isCollapsed || selection.rangeCount === 0) {
        return
      }
      const range = selection.getRangeAt(0)
      if (
        !root.contains(range.startContainer) ||
        !root.contains(range.endContainer)
      ) {
        return
      }
      const beforeStart = range.cloneRange()
      beforeStart.selectNodeContents(root)
      beforeStart.setEnd(range.startContainer, range.startOffset)

      const beforeEnd = range.cloneRange()
      beforeEnd.selectNodeContents(root)
      beforeEnd.setEnd(range.endContainer, range.endOffset)

      const span = codeUnitRangeToEvidenceSpan(chunk.id, chunk.text, {
        start: srcStart + beforeStart.toString().length,
        end: srcStart + beforeEnd.toString().length,
      })
      onSelect(span)
      selection.removeAllRanges()
    },
    [chunk.id, chunk.text, onSelect, readOnly],
  )

  const evidenceSpanForBlock = React.useCallback(
    (srcStart: number, text: string) =>
      codeUnitRangeToEvidenceSpan(chunk.id, chunk.text, {
        start: srcStart,
        end: srcStart + text.length,
      }),
    [chunk.id, chunk.text],
  )

  const blockRangeSpans = React.useCallback(
    (srcStart: number) => {
      const clickedIndex = selectableBlocks.findIndex(
        (block) => block.srcStart === srcStart,
      )
      if (clickedIndex < 0 || selectedSpans.length === 0) {
        return []
      }
      const selectedStarts = new Set<number>()
      for (const span of selectedSpans) {
        selectedStarts.add(
          codePointOffsetToCodeUnitOffset(chunk.text, span.start),
        )
      }
      const selectedIndexes = selectableBlocks
        .map((block, index) =>
          selectedStarts.has(block.srcStart) ? index : -1,
        )
        .filter((index) => index >= 0)
      if (selectedIndexes.length === 0) {
        return []
      }
      const anchorIndex = selectedIndexes.reduce((nearest, index) =>
        Math.abs(index - clickedIndex) < Math.abs(nearest - clickedIndex)
          ? index
          : nearest,
      )
      const startIndex = Math.min(anchorIndex, clickedIndex)
      const endIndex = Math.max(anchorIndex, clickedIndex)
      return selectableBlocks
        .slice(startIndex, endIndex + 1)
        .map((block) => evidenceSpanForBlock(block.srcStart, block.text))
    },
    [chunk.text, evidenceSpanForBlock, selectableBlocks, selectedSpans],
  )

  const commitBlock = React.useCallback(
    (
      srcStart: number,
      text: string,
      event?:
        | React.KeyboardEvent<HTMLElement>
        | React.PointerEvent<HTMLElement>,
    ) => {
      if (readOnly) {
        return false
      }
      const span = codeUnitRangeToEvidenceSpan(chunk.id, chunk.text, {
        start: srcStart,
        end: srcStart + text.length,
      })
      const range = Boolean(event?.ctrlKey || event?.metaKey)
      if (onBlockSelect) {
        return (
          onBlockSelect({
            additive: Boolean(
              event?.ctrlKey || event?.metaKey || event?.shiftKey,
            ),
            range,
            rangeSpans: range ? blockRangeSpans(srcStart) : [],
            span,
          }) !== false
        )
      }
      return onSelect(span) !== false
    },
    [blockRangeSpans, chunk.id, chunk.text, onBlockSelect, onSelect, readOnly],
  )

  const activateBlock = React.useCallback(
    (srcStart: number, text: string) =>
      (event: React.KeyboardEvent<HTMLElement>) => {
        if (
          selectionMode !== "block" ||
          (event.key !== "Enter" && event.key !== " ")
        ) {
          return
        }
        event.preventDefault()
        commitBlock(srcStart, text, event)
      },
    [commitBlock, selectionMode],
  )

  const blockInteractionProps = React.useCallback(
    (srcStart: number, text: string, selected: boolean) => {
      if (selectionMode !== "block" || readOnly) {
        return {}
      }
      return {
        "aria-label": `Select evidence block: ${text}`,
        "aria-pressed": selected,
        "data-evidence-block": "true",
        onKeyDown: activateBlock(srcStart, text),
        role: "button",
        tabIndex: 0,
      }
    },
    [activateBlock, readOnly, selectionMode],
  )

  const startBlockDrag = React.useCallback(
    (srcStart: number, text: string) =>
      (event: React.PointerEvent<HTMLElement>) => {
        if (selectionMode !== "block" || event.button !== 0) {
          return
        }
        if (event.altKey && onSearchSelect) {
          return
        }
        if (readOnly) {
          return
        }
        event.preventDefault()
        const key = `${chunk.id}-${srcStart}-${text.length}`
        if (!selectionController.begin(key)) {
          return
        }
        if (!commitBlock(srcStart, text, event)) {
          selectionController.end()
          return
        }
        window.addEventListener(
          "pointerup",
          () => {
            selectionController.end()
          },
          { once: true },
        )
      },
    [
      chunk.id,
      commitBlock,
      onSearchSelect,
      readOnly,
      selectionController,
      selectionMode,
    ],
  )

  const continueBlockDrag = React.useCallback(
    (srcStart: number, text: string) =>
      (event: React.PointerEvent<HTMLElement>) => {
        if (
          selectionMode !== "block" ||
          readOnly ||
          !selectionController.isDragging ||
          event.buttons !== 1
        ) {
          return
        }
        const key = `${chunk.id}-${srcStart}-${text.length}`
        if (!selectionController.visit(key)) {
          return
        }
        if (!commitBlock(srcStart, text, event)) {
          selectionController.end()
        }
      },
    [chunk.id, commitBlock, readOnly, selectionController, selectionMode],
  )

  const selectedBlockSpan = React.useCallback(
    (srcStart: number, text: string) => {
      const start = Array.from(chunk.text.slice(0, srcStart)).length
      const end = start + Array.from(text).length
      return selectedSpans.find(
        (span) => span.start === start && span.end === end,
      )
    },
    [chunk.text, selectedSpans],
  )

  const selectedBlockStarts = React.useMemo(() => {
    const starts = new Set<number>()
    for (const span of selectedSpans) {
      starts.add(codePointOffsetToCodeUnitOffset(chunk.text, span.start))
    }
    return starts
  }, [chunk.text, selectedSpans])

  const selectedBlockCount = selectedBlockStarts.size
  const selectedBlockPosition = React.useCallback(
    (srcStart: number) => {
      const index = selectableBlocks.findIndex(
        (block) => block.srcStart === srcStart,
      )
      if (index < 0 || !selectedBlockStarts.has(srcStart)) {
        return { previous: false, next: false }
      }
      const previousBlock = selectableBlocks[index - 1]
      const nextBlock = selectableBlocks[index + 1]
      return {
        previous:
          previousBlock !== undefined &&
          selectedBlockStarts.has(previousBlock.srcStart),
        next:
          nextBlock !== undefined &&
          selectedBlockStarts.has(nextBlock.srcStart),
      }
    },
    [selectableBlocks, selectedBlockStarts],
  )

  const selectedEdgeBlockStarts = React.useMemo(() => {
    const edgeStarts = new Set<number>()
    selectableBlocks.forEach((block, index) => {
      if (!selectedBlockStarts.has(block.srcStart)) {
        return
      }
      const previousBlock = selectableBlocks[index - 1]
      const nextBlock = selectableBlocks[index + 1]
      const previousSelected =
        previousBlock !== undefined &&
        selectedBlockStarts.has(previousBlock.srcStart)
      const nextSelected =
        nextBlock !== undefined && selectedBlockStarts.has(nextBlock.srcStart)
      if (!previousSelected || !nextSelected) {
        edgeStarts.add(block.srcStart)
      }
    })
    return edgeStarts
  }, [selectableBlocks, selectedBlockStarts])

  const committedBlockSpan = React.useCallback(
    (srcStart: number, text: string) => {
      const start = Array.from(chunk.text.slice(0, srcStart)).length
      const end = start + Array.from(text).length
      return committedSpans.find(
        (span) => span.start === start && span.end === end,
      )
    },
    [chunk.text, committedSpans],
  )

  const removeAllSelectedBlocks = () => {
    if (!onRemove) {
      return
    }
    for (const span of selectedSpans) {
      onRemove(span)
    }
  }

  const removeBlockButton = (
    span: EvidenceSpan | undefined,
    srcStart: number,
  ) =>
    selectionMode === "block" &&
    onRemove &&
    span &&
    selectedBlockCount > 1 &&
    selectedEdgeBlockStarts.has(srcStart) ? (
      <button
        aria-label="Remove this evidence block"
        className="absolute right-2 top-2 z-10 rounded-sm border bg-background/95 px-1.5 py-0.5 text-xs font-semibold leading-none text-primary opacity-90 shadow-xs hover:bg-primary/10"
        key={`block-remove-${srcStart}-${selectedBlockCount}`}
        onClick={(event) => {
          event.preventDefault()
          event.stopPropagation()
          onRemove(span)
        }}
        onPointerDown={(event) => {
          event.preventDefault()
          event.stopPropagation()
        }}
        type="button"
      >
        x
      </button>
    ) : null

  const selectable =
    readOnly && selectionMode === "block"
      ? cn(
          "relative rounded-md border border-transparent px-2 py-1.5",
          searchSelectionActive && "selection:bg-amber-300/70",
        )
      : readOnly
        ? ""
        : selectionMode === "exact"
          ? cn(
              "cursor-text",
              searchSelectionActive
                ? "selection:bg-amber-300/70"
                : "selection:bg-primary/25",
            )
          : cn(
              "relative cursor-pointer rounded-md border border-transparent px-2 py-1.5 outline-none transition hover:border-primary/30 hover:bg-primary/5 focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px]",
              searchSelectionActive && "selection:bg-amber-300/70",
            )
  const selectedBlock = "border-transparent bg-primary/15"
  const selectedBlockGroup = (srcStart: number) => {
    const position = selectedBlockPosition(srcStart)
    return cn(
      selectedBlock,
      selectedBlockCount > 1 &&
        position.previous &&
        "-mt-px rounded-t-none border-t-0",
      selectedBlockCount > 1 && position.next && "rounded-b-none border-b-0",
    )
  }
  const selectedListSpacing = selectedBlockCount > 1 ? "space-y-0" : "space-y-1"
  const committedBlock =
    "border-primary/20 bg-primary/5 shadow-[inset_3px_0_0_hsl(var(--primary)/0.35)]"

  const updateSelectionOverlay = React.useCallback(() => {
    const root = rootRef.current
    if (!root || selectionMode !== "block" || selectedBlockCount === 0) {
      setSelectionOverlayRect(null)
      return
    }

    const selectedElements = selectableBlocks
      .filter((block) => selectedBlockStarts.has(block.srcStart))
      .map((block) => blockRefs.current.get(block.srcStart))
      .filter((element): element is HTMLElement => element !== undefined)

    if (selectedElements.length === 0) {
      setSelectionOverlayRect(null)
      return
    }

    const rootRect = root.getBoundingClientRect()
    const selectedRects = selectedElements.map((element) =>
      element.getBoundingClientRect(),
    )
    const left = Math.min(...selectedRects.map((rect) => rect.left))
    const right = Math.max(...selectedRects.map((rect) => rect.right))
    const top = Math.min(...selectedRects.map((rect) => rect.top))
    const bottom = Math.max(...selectedRects.map((rect) => rect.bottom))
    const nextRect = {
      height: bottom - top,
      left: left - rootRect.left,
      top: top - rootRect.top,
      width: right - left,
    }

    setSelectionOverlayRect((current) =>
      current &&
      Math.abs(current.height - nextRect.height) < 0.5 &&
      Math.abs(current.left - nextRect.left) < 0.5 &&
      Math.abs(current.top - nextRect.top) < 0.5 &&
      Math.abs(current.width - nextRect.width) < 0.5
        ? current
        : nextRect,
    )
  }, [selectableBlocks, selectedBlockCount, selectedBlockStarts, selectionMode])

  React.useLayoutEffect(() => {
    updateSelectionOverlay()
    const root = rootRef.current
    if (!root || selectionMode !== "block" || selectedBlockCount === 0) {
      return
    }

    const observer = new ResizeObserver(() => updateSelectionOverlay())
    observer.observe(root)
    for (const block of selectableBlocks) {
      if (!selectedBlockStarts.has(block.srcStart)) {
        continue
      }
      const element = blockRefs.current.get(block.srcStart)
      if (element) {
        observer.observe(element)
      }
    }
    window.addEventListener("resize", updateSelectionOverlay)
    return () => {
      observer.disconnect()
      window.removeEventListener("resize", updateSelectionOverlay)
    }
  }, [
    selectableBlocks,
    selectedBlockCount,
    selectedBlockStarts,
    selectionMode,
    updateSelectionOverlay,
  ])

  const selectionOverlay =
    selectionOverlayRect && selectionMode === "block" ? (
      <div
        className="pointer-events-none absolute z-10 rounded-md border border-primary/60 bg-primary/5 shadow-[inset_4px_0_0_hsl(var(--primary))]"
        style={{
          height: selectionOverlayRect.height,
          left: selectionOverlayRect.left,
          top: selectionOverlayRect.top,
          width: selectionOverlayRect.width,
        }}
      >
        <div className="pointer-events-auto absolute right-1 top-1 flex flex-col gap-1 sm:-right-11 sm:top-0">
          {onConfirmSelection && selectedBlockCount > 1 ? (
            <button
              aria-label="Confirm selected evidence"
              className="rounded-full border bg-background p-1 text-primary shadow-sm hover:bg-primary/10"
              onClick={(event) => {
                event.preventDefault()
                event.stopPropagation()
                onConfirmSelection()
              }}
              onPointerDown={(event) => {
                event.preventDefault()
                event.stopPropagation()
              }}
              type="button"
            >
              <Check aria-hidden className="size-3.5" />
            </button>
          ) : null}
          {onRemove ? (
            <button
              aria-label="Remove selected evidence"
              className="rounded-full border bg-background px-1.5 py-0.5 text-xs font-semibold leading-none text-primary shadow-sm hover:bg-primary/10"
              onClick={(event) => {
                event.preventDefault()
                event.stopPropagation()
                removeAllSelectedBlocks()
              }}
              onPointerDown={(event) => {
                event.preventDefault()
                event.stopPropagation()
              }}
              type="button"
            >
              x
            </button>
          ) : null}
        </div>
      </div>
    ) : null

  return (
    <fieldset
      className={cn("relative border-0 p-0 text-sm", className)}
      data-chunk-id={chunk.id}
      onKeyUp={commitSearchOrExactSelection}
      onMouseUp={commitSearchOrExactSelection}
      onPointerDown={startSearchSelection}
      onPointerUp={commitSearchOrExactSelection}
      ref={rootRef}
    >
      {selectionOverlay}
      <div className="space-y-3">
        {blocks.map((block) => {
          if (block.kind === "heading") {
            const Tag =
              block.level >= 3 ? "h4" : block.level === 2 ? "h3" : "h2"
            const selectedSpan = selectedBlockSpan(block.srcStart, block.text)
            const committedSpan = committedBlockSpan(block.srcStart, block.text)
            const anchorSpan = selectedSpan ?? committedSpan
            const selected = Boolean(selectedSpan)
            return (
              <Tag
                {...blockInteractionProps(block.srcStart, block.text, selected)}
                className={cn(
                  headingClass[block.level] ?? headingClass[3],
                  selectable,
                  selectionMode === "block" &&
                    committedSpan &&
                    !selected &&
                    (committedSpan.blockClass ?? committedBlock),
                  selectionMode === "block" &&
                    selected &&
                    selectedBlockGroup(block.srcStart),
                )}
                id={
                  anchorSpan
                    ? evidenceAnchorId(
                        chunk.id,
                        anchorSpan.start,
                        anchorSpan.end,
                      )
                    : undefined
                }
                key={block.srcStart}
                onKeyUp={
                  selectionMode === "exact" ? commit(block.srcStart) : undefined
                }
                onMouseUp={
                  selectionMode === "exact" ? commit(block.srcStart) : undefined
                }
                onPointerUp={
                  selectionMode === "exact" ? commit(block.srcStart) : undefined
                }
                onPointerDown={startBlockDrag(block.srcStart, block.text)}
                onPointerEnter={continueBlockDrag(block.srcStart, block.text)}
                ref={setBlockRef(block.srcStart)}
              >
                <SelectableText
                  activeSearchMatchIndex={activeSearchMatchIndex}
                  chunk={chunk}
                  committedSpans={inlineCommittedSpans}
                  onCommittedSelect={onCommittedSelect}
                  onRemove={selectionMode === "exact" ? onRemove : undefined}
                  searchMatches={searchMatches}
                  selectedSpans={inlineSelectedSpans}
                  srcStart={block.srcStart}
                  text={block.text}
                />
                {removeBlockButton(selectedSpan, block.srcStart)}
              </Tag>
            )
          }

          if (block.kind === "list") {
            return (
              <ul
                className={cn(
                  "list-disc pl-5 leading-relaxed",
                  selectedListSpacing,
                )}
                key={block.srcStart}
              >
                {block.items.map((item) => {
                  const selectedSpan = selectedBlockSpan(
                    item.srcStart,
                    item.text,
                  )
                  const committedSpan = committedBlockSpan(
                    item.srcStart,
                    item.text,
                  )
                  const anchorSpan = selectedSpan ?? committedSpan
                  return (
                    <li
                      {...blockInteractionProps(
                        item.srcStart,
                        item.text,
                        Boolean(selectedSpan),
                      )}
                      className={cn(
                        selectable,
                        selectionMode === "block" &&
                          committedSpan &&
                          !selectedSpan &&
                          (committedSpan.blockClass ?? committedBlock),
                        selectionMode === "block" &&
                          selectedSpan &&
                          selectedBlockGroup(item.srcStart),
                      )}
                      id={
                        anchorSpan
                          ? evidenceAnchorId(
                              chunk.id,
                              anchorSpan.start,
                              anchorSpan.end,
                            )
                          : undefined
                      }
                      key={item.srcStart}
                      onKeyUp={
                        selectionMode === "exact"
                          ? commit(item.srcStart)
                          : undefined
                      }
                      onMouseUp={
                        selectionMode === "exact"
                          ? commit(item.srcStart)
                          : undefined
                      }
                      onPointerUp={
                        selectionMode === "exact"
                          ? commit(item.srcStart)
                          : undefined
                      }
                      onPointerDown={startBlockDrag(item.srcStart, item.text)}
                      onPointerEnter={continueBlockDrag(
                        item.srcStart,
                        item.text,
                      )}
                      ref={setBlockRef(item.srcStart)}
                    >
                      <SelectableText
                        activeSearchMatchIndex={activeSearchMatchIndex}
                        chunk={chunk}
                        committedSpans={inlineCommittedSpans}
                        onCommittedSelect={onCommittedSelect}
                        onRemove={
                          selectionMode === "exact" ? onRemove : undefined
                        }
                        searchMatches={searchMatches}
                        selectedSpans={inlineSelectedSpans}
                        srcStart={item.srcStart}
                        text={item.text}
                      />
                      {removeBlockButton(selectedSpan, item.srcStart)}
                    </li>
                  )
                })}
              </ul>
            )
          }

          if (block.kind === "table") {
            const [header, ...rows] = block.rows
            return (
              <div
                className="max-w-full overflow-hidden rounded-md border"
                key={block.srcStart}
              >
                <table className="w-full table-fixed border-collapse text-left text-sm">
                  <thead className="bg-muted/50">
                    <tr>
                      {header.map((cell, index) => {
                        const selectedSpan = selectedBlockSpan(
                          cell.srcStart,
                          cell.text,
                        )
                        const committedSpan = committedBlockSpan(
                          cell.srcStart,
                          cell.text,
                        )
                        const anchorSpan = selectedSpan ?? committedSpan
                        return (
                          <th
                            {...blockInteractionProps(
                              cell.srcStart,
                              cell.text,
                              Boolean(selectedSpan),
                            )}
                            className={cn(
                              "whitespace-normal break-words border-b px-3 py-2 font-semibold align-top [overflow-wrap:anywhere]",
                              selectable,
                              selectionMode === "block" &&
                                committedSpan &&
                                !selectedSpan &&
                                (committedSpan.blockClass ?? committedBlock),
                              selectionMode === "block" &&
                                selectedSpan &&
                                selectedBlockGroup(cell.srcStart),
                            )}
                            id={
                              anchorSpan
                                ? evidenceAnchorId(
                                    chunk.id,
                                    anchorSpan.start,
                                    anchorSpan.end,
                                  )
                                : undefined
                            }
                            key={`${cell.srcStart}-${index}`}
                            onKeyUp={
                              selectionMode === "exact"
                                ? commit(cell.srcStart)
                                : undefined
                            }
                            onMouseUp={
                              selectionMode === "exact"
                                ? commit(cell.srcStart)
                                : undefined
                            }
                            onPointerUp={
                              selectionMode === "exact"
                                ? commit(cell.srcStart)
                                : undefined
                            }
                            onPointerDown={startBlockDrag(
                              cell.srcStart,
                              cell.text,
                            )}
                            onPointerEnter={continueBlockDrag(
                              cell.srcStart,
                              cell.text,
                            )}
                            ref={setBlockRef(cell.srcStart)}
                          >
                            <SelectableText
                              activeSearchMatchIndex={activeSearchMatchIndex}
                              chunk={chunk}
                              committedSpans={inlineCommittedSpans}
                              onCommittedSelect={onCommittedSelect}
                              onRemove={
                                selectionMode === "exact" ? onRemove : undefined
                              }
                              searchMatches={searchMatches}
                              selectedSpans={inlineSelectedSpans}
                              srcStart={cell.srcStart}
                              text={cell.text}
                            />
                            {removeBlockButton(selectedSpan, cell.srcStart)}
                          </th>
                        )
                      })}
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row, rowIndex) => (
                      <tr className="border-b last:border-b-0" key={rowIndex}>
                        {row.map((cell, index) => {
                          const selectedSpan = selectedBlockSpan(
                            cell.srcStart,
                            cell.text,
                          )
                          const committedSpan = committedBlockSpan(
                            cell.srcStart,
                            cell.text,
                          )
                          const anchorSpan = selectedSpan ?? committedSpan
                          return (
                            <td
                              {...blockInteractionProps(
                                cell.srcStart,
                                cell.text,
                                Boolean(selectedSpan),
                              )}
                              className={cn(
                                "whitespace-normal break-words px-3 py-2 align-top [overflow-wrap:anywhere]",
                                selectable,
                                selectionMode === "block" &&
                                  committedSpan &&
                                  !selectedSpan &&
                                  (committedSpan.blockClass ?? committedBlock),
                                selectionMode === "block" &&
                                  selectedSpan &&
                                  selectedBlockGroup(cell.srcStart),
                              )}
                              id={
                                anchorSpan
                                  ? evidenceAnchorId(
                                      chunk.id,
                                      anchorSpan.start,
                                      anchorSpan.end,
                                    )
                                  : undefined
                              }
                              key={`${cell.srcStart}-${index}`}
                              onKeyUp={
                                selectionMode === "exact"
                                  ? commit(cell.srcStart)
                                  : undefined
                              }
                              onMouseUp={
                                selectionMode === "exact"
                                  ? commit(cell.srcStart)
                                  : undefined
                              }
                              onPointerUp={
                                selectionMode === "exact"
                                  ? commit(cell.srcStart)
                                  : undefined
                              }
                              onPointerDown={startBlockDrag(
                                cell.srcStart,
                                cell.text,
                              )}
                              onPointerEnter={continueBlockDrag(
                                cell.srcStart,
                                cell.text,
                              )}
                              ref={setBlockRef(cell.srcStart)}
                            >
                              <SelectableText
                                activeSearchMatchIndex={activeSearchMatchIndex}
                                chunk={chunk}
                                committedSpans={inlineCommittedSpans}
                                onCommittedSelect={onCommittedSelect}
                                onRemove={
                                  selectionMode === "exact"
                                    ? onRemove
                                    : undefined
                                }
                                searchMatches={searchMatches}
                                selectedSpans={inlineSelectedSpans}
                                srcStart={cell.srcStart}
                                text={cell.text}
                              />
                              {removeBlockButton(selectedSpan, cell.srcStart)}
                            </td>
                          )
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          }

          const selectedSpan = selectedBlockSpan(block.srcStart, block.text)
          const committedSpan = committedBlockSpan(block.srcStart, block.text)
          const anchorSpan = selectedSpan ?? committedSpan
          const selected = Boolean(selectedSpan)
          return (
            <p
              {...blockInteractionProps(block.srcStart, block.text, selected)}
              className={cn(
                "leading-relaxed",
                selectable,
                selectionMode === "block" &&
                  committedSpan &&
                  !selected &&
                  (committedSpan.blockClass ?? committedBlock),
                selectionMode === "block" &&
                  selected &&
                  selectedBlockGroup(block.srcStart),
              )}
              id={
                anchorSpan
                  ? evidenceAnchorId(chunk.id, anchorSpan.start, anchorSpan.end)
                  : undefined
              }
              key={block.srcStart}
              onKeyUp={
                selectionMode === "exact" ? commit(block.srcStart) : undefined
              }
              onMouseUp={
                selectionMode === "exact" ? commit(block.srcStart) : undefined
              }
              onPointerUp={
                selectionMode === "exact" ? commit(block.srcStart) : undefined
              }
              onPointerDown={startBlockDrag(block.srcStart, block.text)}
              onPointerEnter={continueBlockDrag(block.srcStart, block.text)}
              ref={setBlockRef(block.srcStart)}
            >
              <SelectableText
                activeSearchMatchIndex={activeSearchMatchIndex}
                chunk={chunk}
                committedSpans={inlineCommittedSpans}
                onCommittedSelect={onCommittedSelect}
                onRemove={selectionMode === "exact" ? onRemove : undefined}
                searchMatches={searchMatches}
                selectedSpans={inlineSelectedSpans}
                srcStart={block.srcStart}
                text={block.text}
              />
              {removeBlockButton(selectedSpan, block.srcStart)}
            </p>
          )
        })}
      </div>
    </fieldset>
  )
}

export const SelectableChunk = React.memo(SelectableChunkView)
