import { ExternalLink, ZoomIn, ZoomOut } from "lucide-react"
import * as React from "react"

import type { ChunkSummary, DocumentDetail } from "@/client"
import { CopyDocumentMarkdownButton } from "@/components/annotation/CopyDocumentMarkdownButton"
import { DocumentSearchControl } from "@/components/annotation/DocumentSearchControl"
import { Button } from "@/components/ui/button"
import {
  type DocumentSearchMatch,
  findDocumentSearchMatches,
  searchMatchesByChunk,
} from "@/lib/documentSearch"
import { sourceDocumentUrl } from "@/utils"
import {
  DocumentSelectionController,
  DocumentSelectionControllerProvider,
} from "./documentSelectionController"

export type DocumentEvidenceViewerChunkContext = {
  activeSearchMatchIndex: number
  onSearchSelect: (text: string) => void
  searchMatchesByChunkId: Map<number, DocumentSearchMatch[]>
  textSizeClass: string
}

type DocumentEvidenceViewerProps = {
  chunks: ChunkSummary[]
  children: (context: DocumentEvidenceViewerChunkContext) => React.ReactNode
  document?: DocumentDetail
  emptyMessage: string
  instructions: React.ReactNode
  toolbarAction?: React.ReactNode
}

function scrollWithinContainer(element: HTMLElement): void {
  let container = element.parentElement
  while (container && container !== document.body) {
    const overflowY = getComputedStyle(container).overflowY
    if (
      (overflowY === "auto" || overflowY === "scroll") &&
      container.scrollHeight > container.clientHeight
    ) {
      const containerRect = container.getBoundingClientRect()
      const elementRect = element.getBoundingClientRect()
      if (
        elementRect.top < containerRect.top ||
        elementRect.bottom > containerRect.bottom
      ) {
        container.scrollBy({
          top:
            elementRect.top -
            containerRect.top -
            (container.clientHeight - elementRect.height) / 2,
          behavior: "smooth",
        })
      }
      return
    }
    container = container.parentElement
  }
}

/**
 * Shared source presentation for retrieval creation and review. Workflow code
 * supplies its selectable chunks through a render child; this component owns
 * only document chrome, search, scrolling, source provenance, and text size.
 */
export const DocumentEvidenceViewer = React.memo(
  function DocumentEvidenceViewer({
    chunks,
    children,
    document,
    emptyMessage,
    instructions,
    toolbarAction,
  }: DocumentEvidenceViewerProps) {
    const rootRef = React.useRef<HTMLDivElement>(null)
    const [activeSearchMatchIndex, setActiveSearchMatchIndex] =
      React.useState(0)
    const [searchOpen, setSearchOpen] = React.useState(false)
    const [searchQuery, setSearchQuery] = React.useState("")
    const [textSize, setTextSize] = React.useState(1)
    const selectionController = React.useMemo(
      () => new DocumentSelectionController(document?.id),
      [document?.id],
    )
    const sourceUrl = sourceDocumentUrl(document)
    const textSizeClass =
      textSize === 0 ? "text-sm" : textSize === 1 ? "text-base" : "text-lg"
    const searchMatches = React.useMemo(
      () => findDocumentSearchMatches(chunks, searchQuery),
      [chunks, searchQuery],
    )
    const searchMatchesByChunkId = React.useMemo(
      () => searchMatchesByChunk(searchMatches),
      [searchMatches],
    )
    const boundedActiveSearchMatchIndex =
      searchMatches.length === 0
        ? 0
        : Math.min(activeSearchMatchIndex, searchMatches.length - 1)
    const onSearchSelect = React.useCallback((text: string) => {
      setSearchQuery(text)
      setActiveSearchMatchIndex(0)
      setSearchOpen(true)
    }, [])

    React.useEffect(() => {
      if (activeSearchMatchIndex !== boundedActiveSearchMatchIndex) {
        setActiveSearchMatchIndex(boundedActiveSearchMatchIndex)
      }
    }, [activeSearchMatchIndex, boundedActiveSearchMatchIndex])

    React.useEffect(() => {
      if (searchMatches.length === 0) {
        return
      }
      const element = rootRef.current?.querySelector<HTMLElement>(
        `[data-search-match-index="${boundedActiveSearchMatchIndex}"]`,
      )
      if (element) {
        scrollWithinContainer(element)
      }
    }, [boundedActiveSearchMatchIndex, searchMatches.length])

    if (chunks.length === 0) {
      return (
        <div className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
          {emptyMessage}
        </div>
      )
    }

    return (
      <div className="relative max-w-5xl space-y-4" ref={rootRef}>
        <div className="sticky top-4 z-10 float-right -mr-14 hidden flex-col gap-2 lg:flex">
          {toolbarAction}
          <DocumentSearchControl
            activeIndex={boundedActiveSearchMatchIndex}
            onActiveIndexChange={setActiveSearchMatchIndex}
            onOpenChange={setSearchOpen}
            onQueryChange={setSearchQuery}
            open={searchOpen}
            query={searchQuery}
            totalMatches={searchMatches.length}
          />
          <CopyDocumentMarkdownButton
            chunks={chunks}
            document={document}
            sourceUrl={sourceUrl}
          />
          <Button
            aria-label="Increase document text size"
            disabled={textSize === 2}
            onClick={() => setTextSize((current) => Math.min(2, current + 1))}
            size="icon-sm"
            type="button"
            variant="outline"
          >
            <ZoomIn />
          </Button>
          <Button
            aria-label="Decrease document text size"
            disabled={textSize === 0}
            onClick={() => setTextSize((current) => Math.max(0, current - 1))}
            size="icon-sm"
            type="button"
            variant="outline"
          >
            <ZoomOut />
          </Button>
        </div>
        <div className="rounded-md border border-primary/30 bg-primary/5 p-4 text-base text-foreground">
          <div className="mb-1 font-semibold text-primary">Instructions</div>
          {instructions}
        </div>
        <div className="rounded-md border bg-background">
          {document ? (
            <div className="border-b p-4 text-3xl font-bold tracking-tight">
              {sourceUrl ? (
                <a
                  className="group inline-flex items-start gap-2 underline-offset-4 hover:text-primary hover:underline"
                  href={sourceUrl}
                  rel="noreferrer"
                  target="_blank"
                >
                  {document.title}
                  <ExternalLink
                    aria-hidden
                    className="mt-1.5 size-5 shrink-0 text-muted-foreground transition-colors group-hover:text-primary"
                  />
                </a>
              ) : (
                document.title
              )}
            </div>
          ) : null}
          <DocumentSelectionControllerProvider controller={selectionController}>
            <div className="space-y-4 p-4">
              {children({
                activeSearchMatchIndex: boundedActiveSearchMatchIndex,
                onSearchSelect,
                searchMatchesByChunkId,
                textSizeClass,
              })}
            </div>
          </DocumentSelectionControllerProvider>
        </div>
      </div>
    )
  },
)
