import { useMutation } from "@tanstack/react-query"
import {
  Check,
  FileText,
  Import,
  Loader2,
  Search,
  Sparkles,
  X,
} from "lucide-react"
import * as React from "react"

import type { DocumentSummary } from "@/client"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import useCustomToast from "@/hooks/useCustomToast"
import { createNiceDocumentFromUrl, type NiceDocument } from "@/lib/nice"
import { cn } from "@/lib/utils"

/**
 * Cap on rendered rows. Source document sets can run to
 * thousands of documents; rendering them all would jank the list. Searching
 * narrows the set, and "I'm feeling lucky" covers random discovery, so a capped
 * window keeps interaction snappy without hiding the ability to reach anything.
 */
const MAX_VISIBLE_RESULTS = 120

function normalize(value: string): string {
  return value.toLowerCase().trim()
}

function niceExternalIdFromUrl(value: string): string | null {
  let parsed: URL
  try {
    parsed = new URL(value.trim())
  } catch {
    return null
  }

  if (
    !["nice.org.uk", "www.nice.org.uk"].includes(parsed.hostname.toLowerCase())
  ) {
    return null
  }

  const match = parsed.pathname.match(/^\/guidance\/((?:ng|cg)\d+)(?:\/|$)/i)
  return match ? `nice-${match[1].toLowerCase()}` : null
}

type SourceDocumentDialogProps = {
  activeDocumentId: number | null
  datasetId: number | null
  documents: DocumentSummary[]
  onSelect: (documentId: number) => void
  onNiceCreated: (document: NiceDocument) => void
  onToggle: (document: DocumentSummary, checked: boolean) => void
  selectedDocumentIds: number[]
  showUsedDocuments: boolean
  onShowUsedDocumentsChange: (showUsed: boolean) => void
}

/**
 * Full-screen document browser for the retrieval create flow. Opens over the
 * page so the (now large) imported source-document set can be searched by title,
 * sampled at random, and multi-selected without crowding the control panel.
 */
export default function SourceDocumentDialog({
  activeDocumentId,
  datasetId,
  documents,
  onSelect,
  onNiceCreated,
  onToggle,
  selectedDocumentIds,
  showUsedDocuments,
  onShowUsedDocumentsChange,
}: SourceDocumentDialogProps) {
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const [open, setOpen] = React.useState(false)
  const [query, setQuery] = React.useState("")
  const [url, setUrl] = React.useState("")
  const [highlightedIndex, setHighlightedIndex] = React.useState(0)
  const listRef = React.useRef<HTMLDivElement>(null)

  const selectedIdSet = React.useMemo(
    () => new Set(selectedDocumentIds),
    [selectedDocumentIds],
  )

  const filtered = React.useMemo(() => {
    const needle = normalize(query)
    if (!needle) {
      return documents
    }
    return documents.filter((document) =>
      normalize(document.title).includes(needle),
    )
  }, [documents, query])

  const visible = React.useMemo(
    () => filtered.slice(0, MAX_VISIBLE_RESULTS),
    [filtered],
  )

  // Keep the highlighted row in range as the result set shrinks while typing.
  React.useEffect(() => {
    setHighlightedIndex((current) =>
      visible.length === 0 ? 0 : Math.min(current, visible.length - 1),
    )
  }, [visible.length])

  const selectedCount = selectedDocumentIds.length

  const choose = (documentId: number) => {
    onSelect(documentId)
    setOpen(false)
  }

  // Imported documents are handed to the parent, which selects and opens them;
  // the picker then closes so the user lands on the document ready to highlight
  // evidence.
  const acquired = (document: NiceDocument) => {
    showSuccessToast(`Loaded ${document.reference}: ${document.title}`)
    onNiceCreated(document)
    setOpen(false)
  }

  const urlMutation = useMutation({
    mutationFn: (niceUrl: string) =>
      createNiceDocumentFromUrl(datasetId, niceUrl),
    onSuccess: (document) => {
      setUrl("")
      acquired(document)
    },
    onError: (error: Error) => showErrorToast(error.message),
  })

  const acquiring = urlMutation.isPending
  const chooseLuckyDocument = () => {
    const hasSearch = normalize(query).length > 0
    const candidates = hasSearch ? filtered : documents
    if (candidates.length === 0) {
      showErrorToast(
        hasSearch
          ? "No matching source documents are available"
          : "No source documents are available",
      )
      return
    }
    const document = candidates[Math.floor(Math.random() * candidates.length)]
    choose(document.id)
  }

  const importUrl = () => {
    const sourceUrl = url.trim()
    if (!sourceUrl) {
      showErrorToast("Enter a source document URL")
      return
    }
    const externalId = niceExternalIdFromUrl(sourceUrl)
    if (externalId) {
      const existing = documents.find(
        (document) => document.external_id.toLowerCase() === externalId,
      )
      if (existing) {
        setUrl("")
        choose(existing.id)
        return
      }
    }
    urlMutation.mutate(sourceUrl)
  }

  const onListKeyDown = (event: React.KeyboardEvent) => {
    if (visible.length === 0) {
      return
    }
    if (event.key === "ArrowDown") {
      event.preventDefault()
      setHighlightedIndex((current) => (current + 1) % visible.length)
    } else if (event.key === "ArrowUp") {
      event.preventDefault()
      setHighlightedIndex(
        (current) => (current - 1 + visible.length) % visible.length,
      )
    } else if (event.key === "Enter") {
      event.preventDefault()
      const target = visible[highlightedIndex]
      if (target) {
        choose(target.id)
      }
    }
  }

  // Scroll the highlighted row into view during keyboard navigation.
  React.useEffect(() => {
    if (!open) {
      return
    }
    const node = listRef.current?.querySelector<HTMLElement>(
      `[data-result-index="${highlightedIndex}"]`,
    )
    node?.scrollIntoView({ block: "nearest" })
  }, [highlightedIndex, open])

  // Reset the transient search/highlight state each time the dialog opens.
  React.useEffect(() => {
    if (open) {
      setQuery("")
      setHighlightedIndex(0)
    }
  }, [open])

  const triggerLabel =
    selectedCount > 0
      ? `${selectedCount} document${selectedCount === 1 ? "" : "s"} selected`
      : "Select document"

  return (
    <Dialog onOpenChange={setOpen} open={open}>
      <DialogTrigger asChild>
        <button
          className="flex w-full items-center justify-between gap-2 rounded-md border bg-transparent px-3 py-2 text-left text-sm text-muted-foreground transition-colors hover:border-primary/40 hover:bg-muted/40"
          type="button"
        >
          <span className="flex items-center gap-2">
            <FileText aria-hidden className="size-4 shrink-0" />
            {triggerLabel}
            {documents.length > 0 ? ` · ${documents.length} available` : ""}
          </span>
          <Search aria-hidden className="size-4 shrink-0" />
        </button>
      </DialogTrigger>
      <DialogContent
        className="flex h-[88vh] w-[min(64rem,95vw)] max-w-none flex-col gap-0 overflow-hidden p-0 sm:max-w-none"
        onKeyDown={onListKeyDown}
      >
        <DialogHeader className="space-y-3 border-b p-4 text-left">
          <div className="space-y-1">
            <DialogTitle>Source documents</DialogTitle>
            <DialogDescription>
              Search the imported set, randomly select an available document, or
              import a source document URL.
            </DialogDescription>
          </div>
          <div className="relative">
            <input
              autoFocus
              className="border-input focus-visible:border-ring focus-visible:ring-ring/50 w-full rounded-md border bg-transparent py-2 pl-9 pr-3 text-sm shadow-xs outline-none focus-visible:ring-[3px]"
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search documents by title…"
              type="text"
              value={query}
            />
            <Search
              aria-hidden
              className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
            />
          </div>
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
            <Button
              className="shrink-0"
              disabled={acquiring || documents.length === 0}
              onClick={chooseLuckyDocument}
              type="button"
              variant="secondary"
            >
              <Sparkles className="size-4" />
              I'm feeling lucky
            </Button>
            <div className="flex flex-1 gap-2">
              <Input
                aria-label="Source document URL"
                onChange={(event) => setUrl(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.preventDefault()
                    importUrl()
                  }
                }}
                placeholder="https://example.org/source-document"
                value={url}
              />
              <Button
                className="shrink-0"
                disabled={acquiring}
                onClick={importUrl}
                type="button"
                variant="outline"
              >
                {urlMutation.isPending ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Import className="size-4" />
                )}
                Import URL
              </Button>
            </div>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-muted-foreground">
            <span>
              {filtered.length === documents.length
                ? `${documents.length} document${documents.length === 1 ? "" : "s"}`
                : `${filtered.length} of ${documents.length} match`}
              {filtered.length > MAX_VISIBLE_RESULTS
                ? ` · showing first ${MAX_VISIBLE_RESULTS}, refine to narrow`
                : ""}
            </span>
            <div className="flex items-center gap-2">
              <Checkbox
                checked={showUsedDocuments}
                id="dialog-show-used-documents"
                onCheckedChange={(checked) =>
                  onShowUsedDocumentsChange(checked === true)
                }
              />
              <Label
                className="text-xs font-normal text-muted-foreground"
                htmlFor="dialog-show-used-documents"
              >
                Show documents I've already used
              </Label>
            </div>
          </div>
        </DialogHeader>

        <div className="min-h-0 flex-1 overflow-y-auto p-2" ref={listRef}>
          {documents.length === 0 ? (
            <p className="p-6 text-center text-sm text-muted-foreground">
              No source documents available. Import a source document URL, or
              show documents you've already used.
            </p>
          ) : visible.length === 0 ? (
            <p className="p-6 text-center text-sm text-muted-foreground">
              No documents match “{query}”.
            </p>
          ) : (
            <ul className="space-y-1">
              {visible.map((document, index) => {
                const selected = selectedIdSet.has(document.id)
                const active = activeDocumentId === document.id
                const highlighted = highlightedIndex === index
                return (
                  <li
                    className={cn(
                      "flex items-center gap-3 rounded-md border border-transparent px-3 py-2.5 text-sm",
                      highlighted && "bg-muted/60",
                      active && "border-primary/50 bg-primary/5",
                    )}
                    data-result-index={index}
                    key={document.id}
                  >
                    <Checkbox
                      aria-label={`Add ${document.title} to selection`}
                      checked={selected}
                      data-testid={`document-checkbox-${document.id}`}
                      onCheckedChange={(checked) =>
                        onToggle(document, checked === true)
                      }
                    />
                    <button
                      className="flex min-w-0 flex-1 flex-col items-start gap-0.5 text-left"
                      onClick={() => choose(document.id)}
                      onMouseEnter={() => setHighlightedIndex(index)}
                      type="button"
                    >
                      <span className="w-full truncate font-medium">
                        {document.title}
                      </span>
                      <span className="text-xs text-muted-foreground">
                        {document.external_id}
                      </span>
                    </button>
                    {active ? (
                      <span className="flex shrink-0 items-center gap-1 text-xs font-medium text-primary">
                        <Check className="size-3.5" />
                        Open
                      </span>
                    ) : selected ? (
                      <span className="shrink-0 text-xs font-medium text-muted-foreground">
                        Selected
                      </span>
                    ) : null}
                  </li>
                )
              })}
            </ul>
          )}
        </div>

        <div className="flex items-center justify-between gap-3 border-t p-4 text-sm">
          <span className="text-muted-foreground">
            {selectedCount > 0
              ? `${selectedCount} selected`
              : "Select documents to highlight evidence"}
          </span>
          <div className="flex items-center gap-2">
            {selectedCount > 0 ? (
              <Button
                onClick={() => {
                  for (const document of documents) {
                    if (selectedIdSet.has(document.id)) {
                      onToggle(document, false)
                    }
                  }
                }}
                type="button"
                variant="ghost"
              >
                <X className="size-4" />
                Clear selection
              </Button>
            ) : null}
            <Button onClick={() => setOpen(false)} type="button">
              Done
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
