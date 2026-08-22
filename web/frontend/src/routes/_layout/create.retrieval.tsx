import { useMutation, useQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { Loader2, Pencil, Trash2, X } from "lucide-react"
import * as React from "react"
import { toast } from "sonner"

import {
  ApiError,
  type CreateRetrievalDraftSubmit,
  CreateService,
  type DocumentSummary,
  type EvidenceSpan,
  type RetrievalCategory,
  type RetrievalSubmissionBatchResponse,
  type ValidationPreview,
} from "@/client"
import {
  EvidenceTray,
  type EvidenceTraySpan,
} from "@/components/annotation/EvidenceTray"
import type { BlockSelection } from "@/components/annotation/SelectableChunk"
import { ValidationMessages } from "@/components/annotation/ValidationMessages"
import { RetrievalEvidenceDocument as RetrievalEvidenceDocumentFeature } from "@/components/Create/RetrievalEvidenceDocument"
import SourceDocumentDialog from "@/components/Create/SourceDocumentDialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { evalItemColor, evalItemDomId } from "@/lib/evalItemPalette"
import { cn } from "@/lib/utils"
import { apiErrorMessage, ProductMessageError } from "@/utils"
export const Route = createFileRoute("/_layout/create/retrieval")({
  component: RetrievalCreate,
  head: () => ({
    meta: [
      {
        title: "Create Retrieval - AMFV Web",
      },
    ],
  }),
})

const categories: Array<{ value: RetrievalCategory; label: string }> = [
  { value: "VERBATIM", label: "Word-for-word answer" },
  { value: "PARAPHRASE", label: "Paragraph-derived answer" },
  { value: "MULTI_CHUNK", label: "Multiple chunks" },
]

type RetrievalEvidenceSpan = EvidenceTraySpan & {
  sourceDocumentId: number
  confirmed?: boolean
}

/** A committed span annotated with its owning eval item and palette colors. */
type CommittedEvidenceSpan = RetrievalEvidenceSpan & {
  itemId?: string
  markClass: string
  blockClass: string
}

type EvidenceTrayName = "gold" | "trap"

type EvidenceUndoAction =
  | { kind: "add"; span: RetrievalEvidenceSpan; tray: EvidenceTrayName }
  | { kind: "remove"; spans: RetrievalEvidenceSpan[]; tray: EvidenceTrayName }

type RetrievalFormValues = {
  question: string
  expectedAnswer: string
  whyNotAnswerable: string
}

type AddedRetrievalEvalItem = RetrievalFormValues & {
  id: string
  category: RetrievalCategory
  documentIds: number[]
  goldSpans: RetrievalEvidenceSpan[]
  trapSpans: RetrievalEvidenceSpan[]
}

class PreviewRejectedError extends Error {
  constructor(readonly preview: ValidationPreview) {
    super("Server validation rejected one or more retrieval items.")
    this.name = "PreviewRejectedError"
  }
}

const EMPTY_COMMITTED_SPANS_BY_CHUNK = new Map<
  number,
  CommittedEvidenceSpan[]
>()
const CONFIRMED_EVIDENCE_MARK_CLASS = "bg-primary/10 ring-primary/15"
const CONFIRMED_EVIDENCE_BLOCK_CLASS = "border-primary/20 bg-primary/5"

function newClientRequestId(): string {
  return (
    globalThis.crypto?.randomUUID?.() ??
    `retrieval-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
  )
}

function evidenceForSubmit(spans: EvidenceTraySpan[]): EvidenceSpan[] {
  return spans.map(({ chunk_id, start, end, text }) => ({
    chunk_id,
    start,
    end,
    text,
  }))
}

function moveSpan<T extends EvidenceTraySpan>(
  spans: T[],
  id: string,
  direction: "up" | "down",
): T[] {
  const index = spans.findIndex((span) => span.id === id)
  const targetIndex = direction === "up" ? index - 1 : index + 1
  if (index < 0 || targetIndex < 0 || targetIndex >= spans.length) {
    return spans
  }
  const nextSpans = [...spans]
  const [span] = nextSpans.splice(index, 1)
  nextSpans.splice(targetIndex, 0, span)
  return nextSpans
}

function evidenceSpanKey(span: EvidenceSpan): string {
  return `${span.chunk_id}:${span.start}:${span.end}`
}

function mergeEvidenceSpans(
  current: RetrievalEvidenceSpan[],
  next: RetrievalEvidenceSpan[],
): RetrievalEvidenceSpan[] {
  const merged = [...current]
  const seen = new Set(current.map(evidenceSpanKey))
  for (const span of next) {
    const key = evidenceSpanKey(span)
    if (!seen.has(key)) {
      merged.push(span)
      seen.add(key)
    }
  }
  return merged
}

function maxEvidenceSelections(
  category: RetrievalCategory,
  tray: "gold" | "trap",
): number | null {
  if (tray === "trap" && category === "ADVERSARIAL") {
    return 1
  }
  if (tray === "gold" && category === "VERBATIM") {
    return 1
  }
  return null
}

function retrievalCategoryLabel(category: RetrievalCategory): string {
  return (
    categories.find((entry) => entry.value === category)?.label ?? "Retrieval"
  )
}

function evidenceAnchorId(span: EvidenceSpan): string {
  return `evidence-${span.chunk_id}-${span.start}-${span.end}`
}

/** Nearest ancestor that actually scrolls vertically, or null if none. */
function scrollableAncestor(element: HTMLElement): HTMLElement | null {
  let node = element.parentElement
  while (node && node !== document.body) {
    const overflowY = getComputedStyle(node).overflowY
    if (
      (overflowY === "auto" || overflowY === "scroll") &&
      node.scrollHeight > node.clientHeight
    ) {
      return node
    }
    node = node.parentElement
  }
  return null
}

/**
 * Bring an element into view by scrolling only its own scroll container, never
 * the window. `Element.scrollIntoView` can bubble up and scroll the page, which
 * (with the sticky sidebar) drags the rest of the layout around; this keeps the
 * document column fixed when revealing a sidebar card.
 */
function scrollWithinContainer(element: HTMLElement): void {
  const container = scrollableAncestor(element)
  if (!container) {
    return
  }
  const containerRect = container.getBoundingClientRect()
  const elementRect = element.getBoundingClientRect()
  if (
    elementRect.top >= containerRect.top &&
    elementRect.bottom <= containerRect.bottom
  ) {
    return
  }
  const offset =
    elementRect.top -
    containerRect.top -
    (container.clientHeight - elementRect.height) / 2
  container.scrollBy({ top: offset, behavior: "smooth" })
}

/**
 * Textarea that grows to fit its content so the full text is always visible.
 * Re-measures whenever the value changes, including external updates such as
 * clearing the field or filling a derived expected answer.
 */
const AutoResizeTextarea = React.forwardRef<
  HTMLTextAreaElement,
  React.ComponentProps<"textarea">
>(function AutoResizeTextarea(
  { className, onChange, value, ...props },
  forwardedRef,
) {
  const innerRef = React.useRef<HTMLTextAreaElement>(null)
  React.useImperativeHandle(
    forwardedRef,
    () => innerRef.current as HTMLTextAreaElement,
  )

  const resize = React.useCallback(() => {
    const element = innerRef.current
    if (!element) {
      return
    }
    element.style.height = "auto"
    element.style.height = `${element.scrollHeight}px`
  }, [])

  // biome-ignore lint/correctness/useExhaustiveDependencies: re-measure height whenever the value changes, including external updates
  React.useLayoutEffect(() => {
    resize()
  }, [resize, value])

  return (
    <textarea
      className={cn("resize-none overflow-hidden", className)}
      onChange={(event) => {
        onChange?.(event)
        resize()
      }}
      ref={innerRef}
      value={value}
      {...props}
    />
  )
})

/**
 * Compact, clickable summary of the documents currently in the working set.
 * Each row activates its document in the viewer, mirrors the active highlight,
 * and can be removed without reopening the full browser.
 */
function SelectedDocumentList({
  activeDocumentId,
  documents,
  onSelect,
  onToggle,
  selectedDocumentIds,
}: {
  activeDocumentId: number | null
  documents: DocumentSummary[]
  onSelect: (documentId: number) => void
  onToggle: (document: DocumentSummary, checked: boolean) => void
  selectedDocumentIds: number[]
}) {
  const selectedDocuments = documents.filter((document) =>
    selectedDocumentIds.includes(document.id),
  )
  if (selectedDocuments.length === 0) {
    return null
  }
  return (
    <div className="space-y-2">
      {selectedDocuments.map((document) => (
        <div
          className={cn(
            "flex items-center gap-2 rounded-md border p-2 text-sm",
            activeDocumentId === document.id &&
              "border-primary/50 bg-primary/5",
          )}
          key={document.id}
        >
          <button
            className="min-w-0 flex-1 truncate text-left"
            onClick={() => onSelect(document.id)}
            type="button"
          >
            {document.title}
          </button>
          <Button
            aria-label={`Remove ${document.title}`}
            onClick={() => onToggle(document, false)}
            size="icon-sm"
            type="button"
            variant="ghost"
          >
            <X />
          </Button>
        </div>
      ))}
    </div>
  )
}

type RetrievalControlPanelProps = {
  activeDocumentId: number | null
  category: RetrievalCategory
  datasetId: number | null
  documents: DocumentSummary[]
  documentsLoading: boolean
  showUsedDocuments: boolean
  goldSpans: RetrievalEvidenceSpan[]
  addedEvalItems: AddedRetrievalEvalItem[]
  derivedExpectedAnswer: string | null
  isAdversarial: boolean
  isBusy: boolean
  localValidationFlags: Array<{ level: string; message: string }>
  onAddEvalItem: (values: RetrievalFormValues) => boolean
  onDeleteEvalItem: (itemId: string) => void
  onDatasetChange: (datasetId: number) => void
  onEditEvalItem: (itemId: string) => void
  onMoveGoldSpan: (id: string, direction: "up" | "down") => void
  onMoveTrapSpan: (id: string, direction: "up" | "down") => void
  onShowUsedDocumentsChange: (showUsed: boolean) => void
  onLocateEvidenceSpan: (span: EvidenceSpan) => void
  onPreview: () => void
  onRemoveGoldSpan: (id: string) => void
  onRemoveTrapSpan: (id: string) => void
  onSelectDocument: (documentId: number) => void
  onSubmit: (values: RetrievalFormValues) => void
  onTargetTrayChange: (tray: "gold" | "trap") => void
  onToggleDocument: (document: DocumentSummary, checked: boolean) => void
  resultMessage: string | null
  retrievalDatasets: Array<{ id: number; display_name: string }>
  selectedDocumentIds: number[]
  selectionInstruction: string
  selectionMode: "exact" | "block"
  targetTray: "gold" | "trap"
  trapSpans: RetrievalEvidenceSpan[]
  validation: ValidationPreview | null
  onCategoryChange: (category: RetrievalCategory) => void
}

function RetrievalControlPanel({
  activeDocumentId,
  addedEvalItems,
  category,
  datasetId,
  derivedExpectedAnswer,
  documents,
  documentsLoading,
  showUsedDocuments,
  goldSpans,
  isAdversarial,
  isBusy,
  localValidationFlags,
  onAddEvalItem,
  onCategoryChange,
  onDeleteEvalItem,
  onDatasetChange,
  onEditEvalItem,
  onLocateEvidenceSpan,
  onPreview,
  onMoveGoldSpan,
  onMoveTrapSpan,
  onShowUsedDocumentsChange,
  onRemoveGoldSpan,
  onRemoveTrapSpan,
  onSelectDocument,
  onSubmit,
  onTargetTrayChange,
  onToggleDocument,
  resultMessage,
  retrievalDatasets,
  selectedDocumentIds,
  selectionInstruction,
  selectionMode,
  targetTray,
  trapSpans,
  validation,
}: RetrievalControlPanelProps) {
  const [question, setQuestion] = React.useState("")
  const [expectedAnswer, setExpectedAnswer] = React.useState("")
  const [whyNotAnswerable, setWhyNotAnswerable] = React.useState("")

  React.useEffect(() => {
    if (derivedExpectedAnswer !== null) {
      setExpectedAnswer(derivedExpectedAnswer)
    }
  }, [derivedExpectedAnswer])

  const formValues = (): RetrievalFormValues => ({
    question,
    expectedAnswer,
    whyNotAnswerable,
  })

  // Tracks which evidence span to jump to next per item, so repeated clicks on
  // a multi-span eval item cycle through its highlights in the document.
  const evidenceCycleRef = React.useRef<Map<string, number>>(new Map())
  const jumpToItemEvidence = (item: AddedRetrievalEvalItem) => {
    const spans = [...item.goldSpans, ...item.trapSpans]
    if (spans.length === 0) {
      return
    }
    const next = evidenceCycleRef.current.get(item.id) ?? 0
    onLocateEvidenceSpan(spans[next % spans.length])
    evidenceCycleRef.current.set(item.id, next + 1)
  }

  const addCurrentEvalItem = () => {
    if (!onAddEvalItem(formValues())) {
      return
    }
    setQuestion("")
    setExpectedAnswer("")
    setWhyNotAnswerable("")
  }

  const editEvalItem = (item: AddedRetrievalEvalItem) => {
    setQuestion(item.question)
    setExpectedAnswer(item.expectedAnswer)
    setWhyNotAnswerable(item.whyNotAnswerable)
    onEditEvalItem(item.id)
  }

  return (
    <aside className="space-y-5 lg:sticky lg:top-0 lg:max-h-[calc(100svh-8rem)] lg:self-start lg:overflow-y-auto lg:pr-1">
      <div className="space-y-2">
        <Label>Dataset</Label>
        <Select
          onValueChange={(value) => onDatasetChange(Number(value))}
          value={datasetId?.toString()}
        >
          <SelectTrigger className="w-full" data-testid="dataset-select">
            <SelectValue placeholder="Select retrieval dataset" />
          </SelectTrigger>
          <SelectContent>
            {retrievalDatasets.map((dataset) => (
              <SelectItem key={dataset.id} value={dataset.id.toString()}>
                {dataset.display_name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="space-y-3">
        <Label>Select Document</Label>
        {documentsLoading ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" />
            Loading documents
          </div>
        ) : (
          <div className="space-y-2">
            <SourceDocumentDialog
              activeDocumentId={activeDocumentId}
              documents={documents}
              onSelect={onSelectDocument}
              onShowUsedDocumentsChange={onShowUsedDocumentsChange}
              onToggle={onToggleDocument}
              selectedDocumentIds={selectedDocumentIds}
              showUsedDocuments={showUsedDocuments}
            />
            <SelectedDocumentList
              activeDocumentId={activeDocumentId}
              documents={documents}
              onSelect={onSelectDocument}
              onToggle={onToggleDocument}
              selectedDocumentIds={selectedDocumentIds}
            />
          </div>
        )}
      </div>

      <div className="space-y-2">
        <Label>Category</Label>
        <Select
          onValueChange={(value: RetrievalCategory) => onCategoryChange(value)}
          value={category}
        >
          <SelectTrigger className="w-full" data-testid="category-select">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {categories.map((entry) => (
              <SelectItem key={entry.value} value={entry.value}>
                {entry.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <div className="rounded-md border border-dashed bg-muted/20 p-3 text-sm text-muted-foreground">
          <div className="mb-1 font-semibold text-foreground">Instructions</div>
          {selectionInstruction}
        </div>
      </div>

      <div className="space-y-4 rounded-md border bg-background p-4 shadow-xs">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-lg font-semibold">Eval Item</h2>
          {addedEvalItems.length > 0 ? (
            <span className="rounded-full bg-primary/10 px-2.5 py-1 text-xs font-medium text-primary">
              {addedEvalItems.length} added
            </span>
          ) : null}
        </div>
        <div className="space-y-2">
          <Label htmlFor="retrieval-question">Question</Label>
          <AutoResizeTextarea
            className="border-input focus-visible:border-ring focus-visible:ring-ring/50 min-h-14 w-full rounded-md border bg-transparent px-3 py-2 text-sm shadow-xs outline-none focus-visible:ring-[3px]"
            id="retrieval-question"
            onChange={(event) => setQuestion(event.target.value)}
            value={question}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="expected-answer">Expected answer</Label>
          <AutoResizeTextarea
            className="border-input focus-visible:border-ring focus-visible:ring-ring/50 min-h-14 w-full rounded-md border bg-transparent px-3 py-2 text-sm shadow-xs outline-none focus-visible:ring-[3px] read-only:bg-muted/30"
            id="expected-answer"
            onChange={(event) => setExpectedAnswer(event.target.value)}
            readOnly={derivedExpectedAnswer !== null}
            value={expectedAnswer}
          />
        </div>
        {isAdversarial ? (
          <div className="space-y-2">
            <Label htmlFor="why-not-answerable">Why not answerable</Label>
            <Input
              id="why-not-answerable"
              onChange={(event) => setWhyNotAnswerable(event.target.value)}
              value={whyNotAnswerable}
            />
          </div>
        ) : null}
        {isAdversarial ? (
          <div className="flex flex-wrap items-center gap-2">
            <Button
              onClick={() => onTargetTrayChange("gold")}
              type="button"
              variant={targetTray === "gold" ? "default" : "outline"}
            >
              Evidence
            </Button>
            <Button
              onClick={() => onTargetTrayChange("trap")}
              type="button"
              variant={targetTray === "trap" ? "default" : "outline"}
            >
              Trap evidence
            </Button>
          </div>
        ) : null}

        <div className="space-y-4">
          <EvidenceTray
            emptyLabel={
              selectionMode === "exact"
                ? "Highlight the exact answer text"
                : "Click paragraph evidence"
            }
            onLocate={onLocateEvidenceSpan}
            onMove={onMoveGoldSpan}
            onRemove={onRemoveGoldSpan}
            spans={goldSpans}
            title="Evidence"
          />
          {isAdversarial ? (
            <EvidenceTray
              emptyLabel="Click one trap evidence paragraph"
              onLocate={onLocateEvidenceSpan}
              onMove={onMoveTrapSpan}
              onRemove={onRemoveTrapSpan}
              spans={trapSpans}
              title="Trap evidence"
            />
          ) : null}
        </div>

        <Button
          className="w-full"
          disabled={datasetId === null || isBusy}
          onClick={addCurrentEvalItem}
          type="button"
          variant="secondary"
        >
          Add eval item
        </Button>
      </div>

      {addedEvalItems.length > 0 ? (
        <div className="space-y-2">
          <Label>Added eval items</Label>
          <div className="space-y-2">
            {addedEvalItems.map((item, index) => {
              const color = evalItemColor(index)
              return (
                <div
                  className={cn(
                    "scroll-mt-24 rounded-md border border-l-4 p-3 transition-shadow",
                    color.card,
                  )}
                  id={evalItemDomId(item.id)}
                  key={item.id}
                >
                  <div className="flex items-start justify-between gap-3">
                    <button
                      className="flex min-w-0 flex-1 cursor-pointer flex-col items-start gap-1 text-left text-sm"
                      onClick={() => jumpToItemEvidence(item)}
                      onMouseDown={(event) => event.preventDefault()}
                      title={
                        item.goldSpans.length + item.trapSpans.length > 1
                          ? "Jump to evidence (click again to cycle)"
                          : "Jump to evidence"
                      }
                      type="button"
                    >
                      <div className="flex items-center gap-2 font-semibold">
                        <span
                          aria-hidden
                          className={cn(
                            "size-2.5 shrink-0 rounded-full",
                            color.dot,
                          )}
                        />
                        {retrievalCategoryLabel(item.category)} #{index + 1}
                      </div>
                      <div className="line-clamp-2">
                        <span className="font-medium">Q: </span>
                        {item.question}
                      </div>
                      <div className="line-clamp-2 text-muted-foreground">
                        <span className="font-medium text-foreground">A: </span>
                        {item.expectedAnswer || "Unanswerable"}
                      </div>
                      <div className="text-xs text-muted-foreground">
                        {item.goldSpans.length} gold
                        {item.trapSpans.length > 0
                          ? `, ${item.trapSpans.length} trap`
                          : ""}
                      </div>
                    </button>
                    <div className="flex shrink-0 gap-1">
                      <Button
                        aria-label={`Edit item ${index + 1}`}
                        onClick={() => editEvalItem(item)}
                        size="icon-sm"
                        type="button"
                        variant="ghost"
                      >
                        <Pencil />
                      </Button>
                      <Button
                        aria-label={`Delete item ${index + 1}`}
                        onClick={() => onDeleteEvalItem(item.id)}
                        size="icon-sm"
                        type="button"
                        variant="ghost"
                      >
                        <Trash2 />
                      </Button>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      ) : null}

      <ValidationMessages
        flags={[...localValidationFlags, ...(validation?.flags ?? [])]}
        ok={localValidationFlags.length === 0 ? validation?.ok : false}
      />

      {resultMessage ? (
        <div className="rounded-md border p-3 text-sm">{resultMessage}</div>
      ) : null}

      <div className="flex flex-wrap gap-2">
        <Button
          disabled={datasetId === null || isBusy || addedEvalItems.length === 0}
          onClick={onPreview}
          type="button"
          variant="outline"
        >
          Validate batch
        </Button>
        <Button
          disabled={datasetId === null || isBusy}
          onClick={() => onSubmit(formValues())}
          type="button"
        >
          Submit
        </Button>
      </div>
    </aside>
  )
}

function RetrievalCreate() {
  const [datasetId, setDatasetId] = React.useState<number | null>(null)
  const [selectedDocumentIds, setSelectedDocumentIds] = React.useState<
    number[]
  >([])
  const [showUsedDocuments, setShowUsedDocuments] = React.useState(false)
  const [activeDocumentId, setActiveDocumentId] = React.useState<number | null>(
    null,
  )
  const [category, setCategory] = React.useState<RetrievalCategory>("VERBATIM")
  const [targetTray, setTargetTray] = React.useState<"gold" | "trap">("gold")
  const [goldSpans, setGoldSpans] = React.useState<RetrievalEvidenceSpan[]>([])
  const [trapSpans, setTrapSpans] = React.useState<RetrievalEvidenceSpan[]>([])
  const [addedEvalItems, setAddedEvalItems] = React.useState<
    AddedRetrievalEvalItem[]
  >([])
  const [undoStack, setUndoStack] = React.useState<EvidenceUndoAction[]>([])
  const [validation, setValidation] = React.useState<ValidationPreview | null>(
    null,
  )
  const [localValidationFlags, setLocalValidationFlags] = React.useState<
    Array<{ level: string; message: string }>
  >([])
  const [resultMessage, setResultMessage] = React.useState<string | null>(null)
  const blockedSelectionToastRef = React.useRef<string | null>(null)
  const batchRequestIdRef = React.useRef<string | null>(null)
  const isAdversarial = category === "ADVERSARIAL"
  const selectionMode = category === "VERBATIM" ? "exact" : "block"
  const selectionInstruction =
    selectionMode === "exact"
      ? "Highlight the exact word-for-word answer text in the source document."
      : isAdversarial
        ? "Click the evidence block that contains the distracting trap evidence."
        : category === "PARAPHRASE"
          ? "Click the evidence block that supports the answer. Ctrl/Cmd-click to add adjacent blocks to the selection."
          : "Click or drag across supporting evidence blocks. Ctrl/Cmd-click to add adjacent blocks; use the check to lock a group before selecting the next."
  const derivedExpectedAnswer =
    selectionMode === "exact"
      ? goldSpans.map((span) => span.text).join("\n\n")
      : null
  const selectedSpansByChunk = React.useMemo(() => {
    const spansByChunk = new Map<number, RetrievalEvidenceSpan[]>()
    for (const span of [...goldSpans, ...trapSpans]) {
      if (span.confirmed) {
        continue
      }
      const current = spansByChunk.get(span.chunk_id) ?? []
      current.push(span)
      spansByChunk.set(span.chunk_id, current)
    }
    return spansByChunk
  }, [goldSpans, trapSpans])
  const activeCommittedSpansByChunk = React.useMemo(() => {
    const spansByChunk = new Map<number, CommittedEvidenceSpan[]>()
    for (const span of [...goldSpans, ...trapSpans]) {
      if (!span.confirmed) {
        continue
      }
      const current = spansByChunk.get(span.chunk_id) ?? []
      current.push({
        ...span,
        markClass: CONFIRMED_EVIDENCE_MARK_CLASS,
        blockClass: CONFIRMED_EVIDENCE_BLOCK_CLASS,
      })
      spansByChunk.set(span.chunk_id, current)
    }
    return spansByChunk.size > 0 ? spansByChunk : EMPTY_COMMITTED_SPANS_BY_CHUNK
  }, [goldSpans, trapSpans])

  const savedEvalItemSpansByChunk = React.useMemo(() => {
    if (addedEvalItems.length === 0) {
      return EMPTY_COMMITTED_SPANS_BY_CHUNK
    }
    const spansByChunk = new Map<number, CommittedEvidenceSpan[]>()
    addedEvalItems.forEach((item, index) => {
      const color = evalItemColor(index)
      for (const span of [...item.goldSpans, ...item.trapSpans]) {
        const current = spansByChunk.get(span.chunk_id) ?? []
        current.push({
          ...span,
          itemId: item.id,
          markClass: color.mark,
          blockClass: color.block,
        })
        spansByChunk.set(span.chunk_id, current)
      }
    })
    return spansByChunk
  }, [addedEvalItems])

  const committedSpansByChunk = React.useMemo(() => {
    if (activeCommittedSpansByChunk.size === 0) {
      return savedEvalItemSpansByChunk
    }
    if (savedEvalItemSpansByChunk.size === 0) {
      return activeCommittedSpansByChunk
    }
    const spansByChunk = new Map<number, CommittedEvidenceSpan[]>()
    for (const [chunkId, spans] of savedEvalItemSpansByChunk) {
      spansByChunk.set(chunkId, spans)
    }
    for (const [chunkId, spans] of activeCommittedSpansByChunk) {
      const current = spansByChunk.get(chunkId)
      spansByChunk.set(chunkId, current ? [...current, ...spans] : spans)
    }
    return spansByChunk
  }, [activeCommittedSpansByChunk, savedEvalItemSpansByChunk])

  const optionsQuery = useQuery({
    queryKey: ["create-options"],
    queryFn: CreateService.readCreateOptions,
  })

  const retrievalDatasets =
    optionsQuery.data?.filter((dataset) => dataset.eval_type === "RETRIEVAL") ??
    []

  React.useEffect(() => {
    if (datasetId !== null || retrievalDatasets.length === 0) {
      return
    }
    const defaultDataset =
      retrievalDatasets.length === 1 ? retrievalDatasets[0] : null
    if (defaultDataset) {
      setDatasetId(defaultDataset.id)
    }
  }, [datasetId, retrievalDatasets])

  const documentsQuery = useQuery({
    queryKey: ["create-source-documents", datasetId, showUsedDocuments],
    queryFn: () =>
      CreateService.readSourceDocuments({
        datasetId: datasetId as number,
        evalType: "RETRIEVAL",
        includeUsed: showUsedDocuments,
      }),
    enabled: datasetId !== null,
  })

  const activeDocumentQuery = useQuery({
    queryKey: ["create-source-document-detail", datasetId, activeDocumentId],
    queryFn: () =>
      CreateService.readSourceDocumentDetail({
        datasetId: datasetId as number,
        documentId: activeDocumentId as number,
        evalType: "RETRIEVAL",
      }),
    enabled: datasetId !== null && activeDocumentId !== null,
  })

  const requestBody = (
    status: "DRAFT" | "SUBMITTED",
    values: RetrievalFormValues,
    item?: AddedRetrievalEvalItem,
  ): CreateRetrievalDraftSubmit => ({
    dataset_id: datasetId ?? 0,
    document_ids: item?.documentIds ?? selectedDocumentIds,
    category: item?.category ?? category,
    question: values.question,
    expected_answer: values.expectedAnswer || null,
    unanswerable: (item?.category ?? category) === "ADVERSARIAL",
    gold_evidence_spans: evidenceForSubmit(item?.goldSpans ?? goldSpans),
    trap_evidence_spans:
      (item?.category ?? category) === "ADVERSARIAL"
        ? evidenceForSubmit(item?.trapSpans ?? trapSpans)
        : [],
    why_not_answerable:
      (item?.category ?? category) === "ADVERSARIAL"
        ? values.whyNotAnswerable || null
        : null,
    status,
  })

  const previewRetrievalItems = async (
    items: AddedRetrievalEvalItem[],
  ): Promise<ValidationPreview[]> =>
    Promise.all(
      items.map((item) =>
        CreateService.previewRetrievalCreation({
          requestBody: requestBody("DRAFT", item, item),
        }),
      ),
    )

  const previewMutation = useMutation({
    mutationFn: previewRetrievalItems,
    onSuccess: (previews) => {
      setLocalValidationFlags([])
      setValidation(previews[previews.length - 1] ?? null)
      setResultMessage("Server validation completed.")
    },
    onError: (error) => setResultMessage(apiErrorMessage(error)),
  })

  const submitMutation = useMutation({
    mutationFn: async (items: AddedRetrievalEvalItem[]) => {
      const previews = await previewRetrievalItems(items)
      const rejected = previews.find((preview) => !preview.ok)
      if (rejected) {
        throw new PreviewRejectedError(rejected)
      }
      const requestId = batchRequestIdRef.current ?? newClientRequestId()
      batchRequestIdRef.current = requestId
      try {
        const receipt = await CreateService.submitRetrievalBatch({
          requestBody: {
            request_id: requestId,
            items: items.map((item) => requestBody("SUBMITTED", item, item)),
          },
        })
        return { previews, receipt, reconciled: false }
      } catch (error) {
        if (error instanceof ApiError) {
          throw error
        }
        try {
          const receipt: RetrievalSubmissionBatchResponse =
            await CreateService.readRetrievalBatch({ requestId })
          return { previews, receipt, reconciled: true }
        } catch (_reconciliationError) {
          throw new ProductMessageError(
            "Submission outcome is unknown. It was not retried automatically; reconcile the saved receipt before sending another batch.",
          )
        }
      }
    },
    onSuccess: ({ previews, receipt, reconciled }) => {
      setLocalValidationFlags([])
      setValidation(previews[previews.length - 1] ?? null)
      setAddedEvalItems([])
      batchRequestIdRef.current = null
      setResultMessage(
        reconciled
          ? `Recovered submission receipt for ${receipt.item_ids.length} item${receipt.item_ids.length === 1 ? "" : "s"}.`
          : `Submitted ${receipt.item_ids.length} item${receipt.item_ids.length === 1 ? "" : "s"}.`,
      )
    },
    onError: (error) => {
      if (error instanceof PreviewRejectedError) {
        setValidation(error.preview)
        setResultMessage(
          "Server validation must pass before this batch can submit.",
        )
        return
      }
      setResultMessage(apiErrorMessage(error))
    },
  })

  const resetDocumentState = (nextDatasetId: number) => {
    setDatasetId(nextDatasetId)
    setSelectedDocumentIds([])
    setActiveDocumentId(null)
    setGoldSpans([])
    setTrapSpans([])
    setAddedEvalItems([])
    setUndoStack([])
    setValidation(null)
    setLocalValidationFlags([])
    setResultMessage(null)
    batchRequestIdRef.current = null
  }

  const setRetrievalCategory = (nextCategory: RetrievalCategory) => {
    if (nextCategory === category) {
      return
    }
    const hasActiveSelection = [...goldSpans, ...trapSpans].some(
      (span) => !span.confirmed,
    )
    if (hasActiveSelection) {
      const message =
        "Clear the current evidence selection before changing answer type."
      setLocalValidationFlags([{ level: "error", message }])
      toast.info(message, {
        description:
          "Use the selection x, or confirm the multi-block selection first.",
        id: "retrieval-category-selection-blocked",
      })
      return
    }
    setCategory(nextCategory)
    if (nextCategory === "ADVERSARIAL") {
      setTargetTray("trap")
    } else {
      setTargetTray("gold")
      setTrapSpans([])
    }
    setUndoStack([])
    setValidation(null)
    setLocalValidationFlags([])
    setResultMessage(null)
  }

  const selectDocument = (documentId: number) => {
    setSelectedDocumentIds((current) =>
      current.includes(documentId) ? current : [...current, documentId],
    )
    setActiveDocumentId(documentId)
    setUndoStack([])
    setLocalValidationFlags([])
  }

  const locateEvidenceSpan = React.useCallback((span: EvidenceSpan) => {
    const element = document.getElementById(evidenceAnchorId(span))
    if (!element) {
      return
    }
    element.scrollIntoView({ behavior: "smooth", block: "center" })
    element.classList.add("ring-2", "ring-offset-1", "ring-foreground")
    window.setTimeout(() => {
      element.classList.remove("ring-2", "ring-offset-1", "ring-foreground")
    }, 1000)
  }, [])

  const scrollToEvalItem = React.useCallback((itemId: string) => {
    const element = document.getElementById(evalItemDomId(itemId))
    if (!element) {
      return
    }
    // Scroll only the sidebar's own container so the clicked document text
    // stays put instead of the whole page scrolling it out of view.
    scrollWithinContainer(element)
    element.classList.add("ring-2", "ring-offset-2", "ring-primary")
    window.setTimeout(() => {
      element.classList.remove("ring-2", "ring-offset-2", "ring-primary")
    }, 1200)
  }, [])

  const toggleDocument = (document: DocumentSummary, checked: boolean) => {
    setSelectedDocumentIds((current) => {
      const next = checked
        ? [...new Set([...current, document.id])]
        : current.filter((id) => id !== document.id)
      if (checked) {
        setActiveDocumentId(document.id)
      } else if (activeDocumentId === document.id) {
        setActiveDocumentId(next[0] ?? null)
      }
      return next
    })
    if (!checked) {
      setUndoStack([])
      setGoldSpans((current) =>
        current.filter((span) => span.sourceDocumentId !== document.id),
      )
      setTrapSpans((current) =>
        current.filter((span) => span.sourceDocumentId !== document.id),
      )
    }
    setValidation(null)
    setLocalValidationFlags([])
    setResultMessage(null)
  }

  const addCurrentEvalItem = React.useCallback(
    (values: RetrievalFormValues) => {
      // These local controls keep selection pleasant, but the preview endpoint
      // is the sole authority for category and evidence acceptance.
      setLocalValidationFlags([])
      setValidation(null)
      setAddedEvalItems((current) => [
        ...current,
        {
          ...values,
          id: `${Date.now()}-${current.length}`,
          category,
          documentIds: selectedDocumentIds,
          goldSpans,
          trapSpans: isAdversarial ? trapSpans : [],
        },
      ])
      setGoldSpans([])
      setTrapSpans([])
      setUndoStack([])
      setResultMessage(null)
      batchRequestIdRef.current = null
      return true
    },
    [category, goldSpans, isAdversarial, selectedDocumentIds, trapSpans],
  )

  const deleteEvalItem = React.useCallback((itemId: string) => {
    setAddedEvalItems((current) => current.filter((item) => item.id !== itemId))
    setValidation(null)
    setLocalValidationFlags([])
    setResultMessage(null)
    batchRequestIdRef.current = null
  }, [])

  const editEvalItem = React.useCallback(
    (itemId: string) => {
      const item = addedEvalItems.find((entry) => entry.id === itemId)
      if (!item) {
        return
      }
      setAddedEvalItems((current) =>
        current.filter((entry) => entry.id !== itemId),
      )
      setCategory(item.category)
      setTargetTray(item.category === "ADVERSARIAL" ? "trap" : "gold")
      setSelectedDocumentIds(item.documentIds)
      setActiveDocumentId(item.documentIds[0] ?? null)
      setGoldSpans(item.goldSpans)
      setTrapSpans(item.trapSpans)
      setUndoStack([])
      setValidation(null)
      setLocalValidationFlags([])
      setResultMessage(null)
      batchRequestIdRef.current = null
    },
    [addedEvalItems],
  )

  const pushUndoAction = React.useCallback((action: EvidenceUndoAction) => {
    setUndoStack((current) => [...current, action].slice(-20))
  }, [])

  const removeEvidenceSpan = React.useCallback(
    (
      span: EvidenceSpan & { id?: string },
      options?: { recordUndo?: boolean },
    ) => {
      const recordUndo = options?.recordUndo !== false
      const matches = (entry: RetrievalEvidenceSpan) =>
        span.id
          ? entry.id === span.id
          : entry.chunk_id === span.chunk_id &&
            entry.start === span.start &&
            entry.end === span.end
      const removedGold = goldSpans.filter(matches)
      const removedTrap = trapSpans.filter(matches)
      if (recordUndo && removedGold.length > 0) {
        pushUndoAction({ kind: "remove", spans: removedGold, tray: "gold" })
      }
      if (recordUndo && removedTrap.length > 0) {
        pushUndoAction({ kind: "remove", spans: removedTrap, tray: "trap" })
      }
      setGoldSpans((current) => current.filter((entry) => !matches(entry)))
      setTrapSpans((current) => current.filter((entry) => !matches(entry)))
      setValidation(null)
      setLocalValidationFlags([])
      setResultMessage(null)
    },
    [goldSpans, pushUndoAction, trapSpans],
  )

  const undoEvidenceChange = React.useCallback(() => {
    const action = undoStack[undoStack.length - 1]
    if (!action) {
      return
    }
    setUndoStack((current) => current.slice(0, -1))
    if (action.kind === "add") {
      removeEvidenceSpan(action.span, { recordUndo: false })
      return
    }
    if (action.tray === "trap") {
      setTrapSpans((current) => [...current, ...action.spans])
    } else {
      setGoldSpans((current) => [...current, ...action.spans])
    }
    setValidation(null)
    setLocalValidationFlags([])
    setResultMessage(null)
  }, [removeEvidenceSpan, undoStack])

  const removeEvidenceSpanById = React.useCallback(
    (id: string) => {
      const span =
        goldSpans.find((entry) => entry.id === id) ??
        trapSpans.find((entry) => entry.id === id)
      if (span) {
        removeEvidenceSpan(span)
      }
    },
    [goldSpans, removeEvidenceSpan, trapSpans],
  )

  const materializeEvidenceSpan = React.useCallback(
    (span: EvidenceSpan): RetrievalEvidenceSpan | null => {
      const sourceDocumentId = activeDocumentQuery.data?.id
      if (sourceDocumentId === undefined) {
        return null
      }
      return {
        ...span,
        id: `${span.chunk_id}-${span.start}-${span.end}-${Date.now()}-${Math.random()
          .toString(36)
          .slice(2)}`,
        label: activeDocumentQuery.data?.title ?? `Chunk ${span.chunk_id}`,
        sourceDocumentId,
      }
    },
    [activeDocumentQuery.data],
  )

  const addEvidenceSpan = React.useCallback(
    (span: EvidenceSpan) => {
      const selectedSpan = materializeEvidenceSpan(span)
      if (!selectedSpan) {
        return false
      }
      const maximum = maxEvidenceSelections(category, targetTray)
      const currentCount =
        targetTray === "trap" ? trapSpans.length : goldSpans.length
      if (maximum !== null && currentCount >= maximum) {
        const message =
          targetTray === "trap"
            ? "Finish this eval item before selecting another trap evidence block."
            : "Finish this eval item before selecting another evidence span."
        setLocalValidationFlags([
          {
            level: "error",
            message,
          },
        ])
        if (blockedSelectionToastRef.current !== message) {
          blockedSelectionToastRef.current = message
          toast.info(message, {
            description:
              "Add the current eval item, or remove the active evidence with the x first.",
            id: "retrieval-selection-blocked",
          })
        }
        return false
      }
      if (targetTray === "trap") {
        setTrapSpans((current) => [...current, selectedSpan])
        pushUndoAction({ kind: "add", span: selectedSpan, tray: "trap" })
      } else {
        setGoldSpans((current) => [...current, selectedSpan])
        pushUndoAction({ kind: "add", span: selectedSpan, tray: "gold" })
      }
      setValidation(null)
      setLocalValidationFlags([])
      blockedSelectionToastRef.current = null
      setResultMessage(null)
      return true
    },
    [
      category,
      goldSpans.length,
      materializeEvidenceSpan,
      pushUndoAction,
      targetTray,
      trapSpans.length,
    ],
  )

  const confirmEvidenceSelection = React.useCallback(() => {
    const confirmSpan = (span: RetrievalEvidenceSpan) =>
      span.confirmed ? span : { ...span, confirmed: true }
    if (targetTray === "trap") {
      setTrapSpans((current) => current.map(confirmSpan))
    } else {
      setGoldSpans((current) => current.map(confirmSpan))
    }
    setUndoStack([])
    setValidation(null)
    setLocalValidationFlags([])
    blockedSelectionToastRef.current = null
    setResultMessage(null)
  }, [targetTray])

  const selectBlockEvidence = React.useCallback(
    (selection: BlockSelection) => {
      const spans =
        selection.range && selection.rangeSpans.length > 0
          ? selection.rangeSpans
          : [selection.span]
      const selectedSpans = spans
        .map(materializeEvidenceSpan)
        .filter((span): span is RetrievalEvidenceSpan => span !== null)
      if (selectedSpans.length === 0) {
        return false
      }

      if (targetTray === "trap") {
        const nextTrapSpans = [selectedSpans[selectedSpans.length - 1]]
        setTrapSpans((current) => [
          ...current.filter((span) => span.confirmed),
          ...nextTrapSpans,
        ])
        setUndoStack([])
        setValidation(null)
        setLocalValidationFlags([])
        blockedSelectionToastRef.current = null
        setResultMessage(null)
        return true
      }

      if (category === "PARAPHRASE") {
        setGoldSpans((current) => {
          const confirmed = current.filter((span) => span.confirmed)
          const active = current.filter((span) => !span.confirmed)
          return [
            ...confirmed,
            ...(selection.additive || selection.range
              ? mergeEvidenceSpans(active, selectedSpans)
              : selectedSpans.slice(-1)),
          ]
        })
        setUndoStack([])
        setValidation(null)
        setLocalValidationFlags([])
        blockedSelectionToastRef.current = null
        setResultMessage(null)
        return true
      }

      if (category === "MULTI_CHUNK" || category === "MULTI_DOCUMENT") {
        setGoldSpans((current) => {
          const confirmed = current.filter((span) => span.confirmed)
          const active = current.filter((span) => !span.confirmed)
          return [...confirmed, ...mergeEvidenceSpans(active, selectedSpans)]
        })
        setUndoStack([])
        setValidation(null)
        setLocalValidationFlags([])
        blockedSelectionToastRef.current = null
        setResultMessage(null)
        return true
      }

      return addEvidenceSpan(selection.span)
    },
    [addEvidenceSpan, category, materializeEvidenceSpan, targetTray],
  )

  const isBusy = submitMutation.isPending || previewMutation.isPending

  const handlePreview = () => {
    if (addedEvalItems.length === 0) {
      setLocalValidationFlags([
        { level: "error", message: "Add at least one eval item to validate." },
      ])
      setValidation(null)
      return
    }
    setLocalValidationFlags([])
    previewMutation.mutate(addedEvalItems)
  }

  const handleSubmit = (values: RetrievalFormValues) => {
    const hasCurrentEvalItemDraft =
      goldSpans.length > 0 ||
      trapSpans.length > 0 ||
      values.question.trim().length > 0 ||
      values.expectedAnswer.trim().length > 0 ||
      values.whyNotAnswerable.trim().length > 0
    const flags = hasCurrentEvalItemDraft
      ? [
          {
            level: "error",
            message: "Add the current eval item before submitting.",
          },
        ]
      : addedEvalItems.length === 0
        ? [{ level: "error", message: "Add at least one eval item." }]
        : []
    setLocalValidationFlags(flags)
    if (flags.length > 0) {
      setValidation(null)
      return
    }
    submitMutation.mutate(addedEvalItems)
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="mx-auto grid w-full max-w-[100rem] gap-6 lg:grid-cols-[32rem_minmax(0,1fr)]">
        <RetrievalControlPanel
          activeDocumentId={activeDocumentId}
          addedEvalItems={addedEvalItems}
          category={category}
          datasetId={datasetId}
          derivedExpectedAnswer={derivedExpectedAnswer}
          documents={documentsQuery.data ?? []}
          documentsLoading={documentsQuery.isLoading}
          showUsedDocuments={showUsedDocuments}
          goldSpans={goldSpans}
          isAdversarial={isAdversarial}
          isBusy={isBusy}
          localValidationFlags={localValidationFlags}
          onAddEvalItem={addCurrentEvalItem}
          onCategoryChange={setRetrievalCategory}
          onDeleteEvalItem={deleteEvalItem}
          onDatasetChange={resetDocumentState}
          onEditEvalItem={editEvalItem}
          onLocateEvidenceSpan={locateEvidenceSpan}
          onPreview={handlePreview}
          onMoveGoldSpan={(id, direction) =>
            setGoldSpans((current) => moveSpan(current, id, direction))
          }
          onMoveTrapSpan={(id, direction) =>
            setTrapSpans((current) => moveSpan(current, id, direction))
          }
          onRemoveGoldSpan={(id) => removeEvidenceSpanById(id)}
          onRemoveTrapSpan={(id) => removeEvidenceSpanById(id)}
          onSelectDocument={selectDocument}
          onShowUsedDocumentsChange={setShowUsedDocuments}
          onSubmit={handleSubmit}
          onTargetTrayChange={setTargetTray}
          onToggleDocument={toggleDocument}
          resultMessage={resultMessage}
          retrievalDatasets={retrievalDatasets}
          selectedDocumentIds={selectedDocumentIds}
          selectionInstruction={selectionInstruction}
          selectionMode={selectionMode}
          targetTray={targetTray}
          trapSpans={trapSpans}
          validation={validation}
        />

        <section className="space-y-6">
          <RetrievalEvidenceDocumentFeature
            canUndoEvidence={undoStack.length > 0}
            committedSpansByChunk={committedSpansByChunk}
            document={activeDocumentQuery.data}
            onCommittedSelect={scrollToEvalItem}
            onConfirmEvidenceSelection={
              category === "MULTI_CHUNK" || category === "MULTI_DOCUMENT"
                ? confirmEvidenceSelection
                : undefined
            }
            onSelectBlockEvidence={selectBlockEvidence}
            onRemoveEvidence={removeEvidenceSpan}
            onSelectEvidence={addEvidenceSpan}
            onUndoEvidence={undoEvidenceChange}
            selectedSpansByChunk={selectedSpansByChunk}
            selectionInstruction={selectionInstruction}
            selectionMode={selectionMode}
          />
        </section>
      </div>
    </div>
  )
}
