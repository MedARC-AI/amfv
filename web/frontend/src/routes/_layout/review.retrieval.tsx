import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { ExternalLink, Loader2, ZoomIn, ZoomOut } from "lucide-react"
import * as React from "react"

import {
  type ChunkSummary,
  type DocumentDetail,
  type EvidenceSpan,
  type RetrievalCategory,
  type RetrievalReviewSubmit,
  ReviewService,
} from "@/client"
import { CopyDocumentMarkdownButton } from "@/components/annotation/CopyDocumentMarkdownButton"
import { DocumentSearchControl } from "@/components/annotation/DocumentSearchControl"
import { SelectableChunk } from "@/components/annotation/SelectableChunk"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import {
  findDocumentSearchMatches,
  searchMatchesByChunk,
} from "@/lib/documentSearch"
import { evalItemDomId } from "@/lib/evalItemPalette"
import { niceDocumentUrl } from "@/lib/nice"
import { cn } from "@/lib/utils"

export const Route = createFileRoute("/_layout/review/retrieval")({
  component: RetrievalReview,
  head: () => ({
    meta: [
      {
        title: "Retrieval Review - AMFV Web",
      },
    ],
  }),
})

type RubricScore = NonNullable<RetrievalReviewSubmit["question_validity"]>
type RubricFieldKey =
  | "question_validity"
  | "evidence_quality"
  | "answer_correctness"
  | "answer_faithfulness"

type RubricFieldDefinition = {
  key: RubricFieldKey
  label: string
  helper: string
  options: Record<RubricScore, string>
}

const RUBRIC_SCORES: RubricScore[] = [1, 2, 3, 4]

const scaleSummary: Record<RubricScore, string> = {
  4: "Excellent",
  3: "Usable with minor issues",
  2: "Significant issues",
  1: "Broken",
}

const rubricFields: RubricFieldDefinition[] = [
  {
    key: "question_validity",
    label: "Question validity",
    helper: "Is the question clear, answerable, and meaningful?",
    options: {
      4: "Clear, unambiguous, answerable from the corpus, and meaningful",
      3: "Minor ambiguity or awkward phrasing, but still answerable",
      2: "Vague, only partially answerable, or too trivial to be useful",
      1: "Unanswerable, incoherent, impossible, or degenerate",
    },
  },
  {
    key: "evidence_quality",
    label: "Evidence quality",
    helper:
      "Is the selected evidence the right evidence for answering the question?",
    options: {
      4: "Relevant, sufficient, and concise",
      3: "Mostly relevant and sufficient, with minor padding or a small gap",
      2: "Significant irrelevant content, missing support, or weak evidence",
      1: "Irrelevant evidence, or missing the key evidence entirely",
    },
  },
  {
    key: "answer_correctness",
    label: "Answer correctness",
    helper: "Does the answer correctly and completely answer the question?",
    options: {
      4: "Correct, complete, and directly responsive",
      3: "Correct but slightly incomplete or imprecise",
      2: "Partially correct, incomplete, or only answers part of the question",
      1: "Incorrect, misleading, or non-responsive",
    },
  },
  {
    key: "answer_faithfulness",
    label: "Answer grounding",
    helper: "Is the answer fully supported by the selected evidence?",
    options: {
      4: "Every substantive claim is supported by the selected evidence",
      3: "Mostly supported, with at most one minor unsupported detail",
      2: "Some important claims are unsupported or overstate the evidence",
      1: "Largely unsupported, contradicted by evidence, or relies on outside knowledge",
    },
  },
]

type CommittedEvidenceSpan = EvidenceSpan & {
  id: string
  label?: string
  itemId?: string
  markClass: string
  blockClass: string
}

const EMPTY_EVIDENCE_SPANS: EvidenceSpan[] = []
const AUTHOR_EVIDENCE_MARK_CLASS = "bg-primary/10 ring-primary/15"
const AUTHOR_EVIDENCE_BLOCK_CLASS = "border-primary/20 bg-primary/5"
const TRAP_EVIDENCE_MARK_CLASS = "bg-destructive/10 ring-destructive/20"
const TRAP_EVIDENCE_BLOCK_CLASS = "border-destructive/20 bg-destructive/5"

function apiMessage(error: unknown): string {
  if (error && typeof error === "object" && "body" in error) {
    const body = (error as { body?: { detail?: unknown } }).body
    if (typeof body?.detail === "string") {
      return body.detail
    }
    if (Array.isArray(body?.detail)) {
      return body.detail
        .map((entry) => {
          if (entry && typeof entry === "object" && "message" in entry) {
            return String((entry as { message: unknown }).message)
          }
          if (entry && typeof entry === "object" && "msg" in entry) {
            return String((entry as { msg: unknown }).msg)
          }
          return String(entry)
        })
        .join("; ")
    }
  }
  if (error instanceof Error) {
    return error.message
  }
  return "Request failed"
}

function evidenceAnchorId(span: EvidenceSpan): string {
  return `evidence-${span.chunk_id}-${span.start}-${span.end}`
}

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

function EvidenceList({
  onLocate,
  spans,
  title,
}: {
  onLocate: (span: EvidenceSpan) => void
  spans: EvidenceSpan[] | undefined
  title: string
}) {
  if (!spans || spans.length === 0) {
    return null
  }
  return (
    <section className="space-y-2">
      <div className="text-sm font-medium">{title}</div>
      <ol className="space-y-2">
        {spans.map((span, index) => (
          <li
            className="rounded-md border bg-muted/30 p-3"
            key={`${title}-${span.chunk_id}-${span.start}-${span.end}-${index}`}
          >
            <button
              className="block w-full text-left"
              onClick={() => onLocate(span)}
              type="button"
            >
              <div className="text-xs text-muted-foreground">
                Chunk {span.chunk_id} · {span.start}-{span.end}
              </div>
              <blockquote className="mt-1 break-words text-sm">
                {span.text}
              </blockquote>
            </button>
          </li>
        ))}
      </ol>
    </section>
  )
}

function chunkLabel(chunk: ChunkSummary): string {
  return `Chunk ${chunk.position + 1}`
}

function categoryLabel(category: RetrievalCategory | null | undefined): string {
  if (category === "VERBATIM") {
    return "Word-for-word answer"
  }
  if (category === "PARAPHRASE") {
    return "Paragraph-derived answer"
  }
  if (category === "MULTI_CHUNK") {
    return "Multiple chunks"
  }
  if (category === "ADVERSARIAL") {
    return "Adversarial unanswerable"
  }
  if (category === "MULTI_DOCUMENT") {
    return "Multiple documents"
  }
  return "Retrieval"
}

function ReviewInstructions() {
  return (
    <div className="space-y-3">
      <div>Review the question, expected answer, and highlighted evidence.</div>
      <div>
        <div>Grade each item from 1 to 4:</div>
        <div>4 = excellent</div>
        <div>3 = usable with minor issues</div>
        <div>2 = significant issues</div>
        <div>1 = broken</div>
      </div>
      <div>
        Then decide whether this example should be accepted as a gold-standard
        reference.
      </div>
    </div>
  )
}

function recommendedGoldDecision(values: RubricValues): boolean | null {
  if (
    values.question_validity === null ||
    values.evidence_quality === null ||
    values.answer_correctness === null ||
    values.answer_faithfulness === null
  ) {
    return null
  }
  return (
    values.question_validity >= 3 &&
    values.evidence_quality >= 3 &&
    values.answer_correctness >= 3 &&
    values.answer_faithfulness >= 3
  )
}

type RubricValues = Record<RubricFieldKey, RubricScore | null>

function RubricScoreField({
  definition,
  onChange,
  value,
}: {
  definition: RubricFieldDefinition
  onChange: (value: RubricScore | null) => void
  value: RubricScore | null
}) {
  return (
    <div className="grid gap-2 sm:grid-cols-[1fr_auto] sm:items-center">
      <div className="min-w-0">
        <Label className="text-sm font-medium">{definition.label}</Label>
        <p className="mt-1 text-xs text-muted-foreground">
          {definition.helper}
        </p>
      </div>
      <div className="grid grid-cols-4 rounded-md border bg-background p-1">
        {RUBRIC_SCORES.map((score) => (
          <label
            className="block"
            key={score}
            title={`${score} — ${definition.options[score]}`}
          >
            <input
              aria-label={`${definition.label}: ${score}`}
              checked={value === score}
              className="sr-only"
              name={`rubric-${definition.key}`}
              onClick={(event) => {
                event.preventDefault()
                onChange(value === score ? null : score)
              }}
              readOnly
              type="radio"
            />
            <span
              className={cn(
                "flex h-8 min-w-9 cursor-pointer items-center justify-center rounded-sm px-2 text-sm font-medium transition",
                value === score
                  ? "bg-primary text-primary-foreground shadow-xs"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              {score}
            </span>
          </label>
        ))}
      </div>
    </div>
  )
}

function RubricDetails() {
  return (
    <details className="rounded-md border bg-muted/20 p-3 text-sm">
      <summary className="cursor-pointer font-medium">Rubric</summary>
      <div className="mt-3 space-y-4">
        <div className="grid grid-cols-2 gap-2 text-xs text-muted-foreground">
          {RUBRIC_SCORES.slice()
            .reverse()
            .map((score) => (
              <div key={score}>
                <span className="font-medium text-foreground">{score}</span> ={" "}
                {scaleSummary[score].toLowerCase()}
              </div>
            ))}
        </div>
        {rubricFields.map((field) => (
          <div className="space-y-1" key={field.key}>
            <div className="font-medium">{field.label}</div>
            {RUBRIC_SCORES.slice()
              .reverse()
              .map((score) => (
                <div className="text-xs text-muted-foreground" key={score}>
                  <span className="font-medium text-foreground">{score}</span> —{" "}
                  {field.options[score]}
                </div>
              ))}
          </div>
        ))}
        <div className="border-t pt-3 text-xs text-muted-foreground">
          Evidence quality measures whether the selected evidence is relevant
          and sufficient. Answer grounding measures whether the answer is fully
          supported by the selected evidence. An answer can be factually correct
          but still poorly grounded if the selected evidence does not support
          it.
        </div>
      </div>
    </details>
  )
}

function allPayloadChunks(
  documents: DocumentDetail[] | undefined,
  chunks: ChunkSummary[] | undefined,
): ChunkSummary[] {
  const chunksById = new Map<number, ChunkSummary>()
  for (const document of documents ?? []) {
    for (const chunk of document.chunks ?? []) {
      chunksById.set(chunk.id, chunk)
    }
  }
  for (const chunk of chunks ?? []) {
    chunksById.set(chunk.id, chunk)
  }
  return [...chunksById.values()]
}

type RetrievalReviewDocumentViewerProps = {
  committedSpansByChunk: Map<number, CommittedEvidenceSpan[]>
  document: DocumentDetail | undefined
  fallbackChunks: ChunkSummary[]
  onCommittedSelect: (itemId: string) => void
  selectionInstruction: React.ReactNode
  selectionMode: "exact" | "block"
}

const RetrievalReviewDocumentViewer = React.memo(
  function RetrievalReviewDocumentViewer({
    committedSpansByChunk,
    document,
    fallbackChunks,
    onCommittedSelect,
    selectionInstruction,
    selectionMode,
  }: RetrievalReviewDocumentViewerProps) {
    const [activeSearchMatchIndex, setActiveSearchMatchIndex] =
      React.useState(0)
    const [searchOpen, setSearchOpen] = React.useState(false)
    const [searchQuery, setSearchQuery] = React.useState("")
    const [textSize, setTextSize] = React.useState(1)
    const sourceUrl = document ? niceDocumentUrl(document.external_id) : null
    const chunks = document?.chunks ?? fallbackChunks
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
    const handleSearchSelect = React.useCallback((text: string) => {
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
      const element = globalThis.document.querySelector<HTMLElement>(
        `[data-search-match-index="${boundedActiveSearchMatchIndex}"]`,
      )
      if (element) {
        scrollWithinContainer(element)
      }
    }, [boundedActiveSearchMatchIndex, searchMatches.length])

    if (chunks.length === 0) {
      return (
        <div className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
          No chunks were attached to this retrieval item.
        </div>
      )
    }

    return (
      <div className="relative max-w-5xl space-y-4">
        <div className="sticky top-4 z-10 float-right -mr-14 hidden flex-col gap-2 lg:flex">
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
          {selectionInstruction}
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
          <div className="space-y-4 p-4">
            {chunks.map((chunk) => (
              <SelectableChunk
                activeSearchMatchIndex={boundedActiveSearchMatchIndex}
                className={textSizeClass}
                chunk={chunk}
                committedSpans={
                  committedSpansByChunk.get(chunk.id) ?? EMPTY_EVIDENCE_SPANS
                }
                key={chunk.id}
                onCommittedSelect={onCommittedSelect}
                onSelect={() => false}
                onSearchSelect={handleSearchSelect}
                readOnly
                searchMatches={searchMatchesByChunkId.get(chunk.id)}
                selectedSpans={EMPTY_EVIDENCE_SPANS}
                selectionMode={selectionMode}
              />
            ))}
          </div>
        </div>
      </div>
    )
  },
)

function RetrievalReview() {
  const queryClient = useQueryClient()
  const [rubricValues, setRubricValues] = React.useState<RubricValues>({
    question_validity: null,
    evidence_quality: null,
    answer_correctness: null,
    answer_faithfulness: null,
  })
  const [wordForWordAnswerEvidenceLinked, setWordForWordAnswerEvidenceLinked] =
    React.useState(true)
  const [acceptAsGold, setAcceptAsGold] = React.useState<boolean | null>(null)
  const [acceptManuallyChanged, setAcceptManuallyChanged] =
    React.useState(false)
  const [notes, setNotes] = React.useState("")
  const [activeDocumentId, setActiveDocumentId] = React.useState<number | null>(
    null,
  )
  const [completionMessage, setCompletionMessage] = React.useState<
    string | null
  >(null)
  const [errorMessage, setErrorMessage] = React.useState<string | null>(null)

  const nextQuery = useQuery({
    queryKey: ["review-next", "retrieval"],
    queryFn: () =>
      ReviewService.readNextReviewTask({
        evalType: "RETRIEVAL",
        mode: "ITEM_AUDIT",
      }),
    retry: false,
  })

  const assignmentId = nextQuery.data?.assignment_id ?? null

  const payloadQuery = useQuery({
    queryKey: ["review-retrieval", assignmentId],
    queryFn: () =>
      ReviewService.readRetrievalReview({
        assignmentId: assignmentId as number,
      }),
    enabled: assignmentId !== null,
    retry: false,
  })

  const payload = payloadQuery.data
  const documents = payload?.documents ?? []
  const chunks = React.useMemo(
    () => allPayloadChunks(payload?.documents, payload?.chunks),
    [payload?.chunks, payload?.documents],
  )
  const chunkById = React.useMemo(() => {
    const next = new Map<number, ChunkSummary>()
    for (const chunk of chunks) {
      next.set(chunk.id, chunk)
    }
    return next
  }, [chunks])
  const documentByChunkId = React.useMemo(() => {
    const next = new Map<number, DocumentDetail>()
    for (const document of documents) {
      for (const chunk of document.chunks ?? []) {
        next.set(chunk.id, document)
      }
    }
    return next
  }, [documents])
  const activeDocument =
    documents.find((document) => document.id === activeDocumentId) ??
    documents[0]
  const fallbackChunks = documents.length === 0 ? chunks : []
  const itemCategory = payload?.item.category ?? null
  const selectionMode = itemCategory === "VERBATIM" ? "exact" : "block"
  const reviewItemId = payload ? `review-item-${payload.item.id}` : null
  const committedSpansByChunk = React.useMemo(() => {
    const spansByChunk = new Map<number, CommittedEvidenceSpan[]>()
    const addCommitted = (
      spans: EvidenceSpan[] | undefined,
      classes: { markClass: string; blockClass: string },
      itemId?: string,
    ) => {
      for (const span of spans ?? []) {
        const current = spansByChunk.get(span.chunk_id) ?? []
        current.push({
          ...span,
          id: `${span.chunk_id}-${span.start}-${span.end}`,
          itemId,
          label: chunkById.has(span.chunk_id)
            ? chunkLabel(chunkById.get(span.chunk_id) as ChunkSummary)
            : `Chunk ${span.chunk_id}`,
          ...classes,
        })
        spansByChunk.set(span.chunk_id, current)
      }
    }
    addCommitted(
      payload?.gold_evidence_spans,
      {
        markClass: AUTHOR_EVIDENCE_MARK_CLASS,
        blockClass: AUTHOR_EVIDENCE_BLOCK_CLASS,
      },
      reviewItemId ?? undefined,
    )
    addCommitted(
      payload?.trap_evidence_spans,
      {
        markClass: TRAP_EVIDENCE_MARK_CLASS,
        blockClass: TRAP_EVIDENCE_BLOCK_CLASS,
      },
      reviewItemId ?? undefined,
    )
    return spansByChunk
  }, [
    chunkById,
    payload?.gold_evidence_spans,
    payload?.trap_evidence_spans,
    reviewItemId,
  ])

  React.useEffect(() => {
    if (!payload) {
      return
    }
    setCompletionMessage(null)
    setErrorMessage(null)
    setRubricValues({
      question_validity: null,
      evidence_quality: null,
      answer_correctness: null,
      answer_faithfulness: null,
    })
    setWordForWordAnswerEvidenceLinked(true)
    setAcceptAsGold(null)
    setAcceptManuallyChanged(false)
    setNotes("")
    setActiveDocumentId(payload.documents?.[0]?.id ?? null)
  }, [payload])

  React.useEffect(() => {
    if (acceptManuallyChanged) {
      return
    }
    setAcceptAsGold(recommendedGoldDecision(rubricValues))
  }, [acceptManuallyChanged, rubricValues])

  const submitMutation = useMutation({
    mutationFn: (requestBody: RetrievalReviewSubmit) =>
      ReviewService.submitRetrievalReview({
        assignmentId: payload?.assignment_id as number,
        requestBody,
      }),
    onSuccess: (response) => {
      setCompletionMessage(`Review submitted for item ${response.item_id}.`)
      setErrorMessage(null)
      queryClient.invalidateQueries({ queryKey: ["review-next", "retrieval"] })
    },
    onError: (error) => setErrorMessage(apiMessage(error)),
  })

  const locateEvidenceSpan = React.useCallback(
    (span: EvidenceSpan) => {
      const targetDocument = documentByChunkId.get(span.chunk_id)
      if (targetDocument && targetDocument.id !== activeDocumentId) {
        setActiveDocumentId(targetDocument.id)
      }
      window.setTimeout(() => {
        const element = document.getElementById(evidenceAnchorId(span))
        if (!element) {
          return
        }
        element.scrollIntoView({ behavior: "smooth", block: "center" })
        element.classList.add("ring-2", "ring-offset-1", "ring-foreground")
        window.setTimeout(() => {
          element.classList.remove("ring-2", "ring-offset-1", "ring-foreground")
        }, 1000)
      })
    },
    [activeDocumentId, documentByChunkId],
  )

  const scrollToReviewItem = React.useCallback((itemId: string) => {
    const element = document.getElementById(evalItemDomId(itemId))
    if (!element) {
      return
    }
    scrollWithinContainer(element)
    element.classList.add("ring-2", "ring-offset-2", "ring-primary")
    window.setTimeout(() => {
      element.classList.remove("ring-2", "ring-offset-2", "ring-primary")
    }, 1200)
  }, [])

  const updateRubricValue = (
    key: RubricFieldKey,
    value: RubricScore | null,
  ) => {
    let shouldBreakWordForWordLink = false
    setRubricValues((current) => {
      const isWordForWordPair =
        itemCategory === "VERBATIM" &&
        (key === "evidence_quality" || key === "answer_correctness")
      if (!isWordForWordPair || !wordForWordAnswerEvidenceLinked) {
        return { ...current, [key]: value }
      }

      const otherKey =
        key === "evidence_quality" ? "answer_correctness" : "evidence_quality"
      const pairIsEmpty = current[key] === null && current[otherKey] === null
      const pairIsSynced =
        current[key] !== null && current[key] === current[otherKey]

      if (pairIsEmpty && value !== null) {
        return {
          ...current,
          evidence_quality: value,
          answer_correctness: value,
        }
      }

      if (pairIsSynced) {
        shouldBreakWordForWordLink = true
        return { ...current, [key]: value }
      }

      return { ...current, [key]: value }
    })
    if (shouldBreakWordForWordLink) {
      setWordForWordAnswerEvidenceLinked(false)
    }
    setErrorMessage(null)
  }

  const setGoldDecision = (value: boolean) => {
    setAcceptAsGold(value)
    setAcceptManuallyChanged(true)
    setErrorMessage(null)
  }

  const canSubmit =
    rubricValues.question_validity !== null &&
    rubricValues.evidence_quality !== null &&
    rubricValues.answer_correctness !== null &&
    rubricValues.answer_faithfulness !== null &&
    acceptAsGold !== null &&
    !submitMutation.isPending &&
    completionMessage === null

  const submitReview = () => {
    if (!payload || !canSubmit) {
      return
    }
    const {
      question_validity,
      evidence_quality,
      answer_correctness,
      answer_faithfulness,
    } = rubricValues
    if (
      question_validity === null ||
      evidence_quality === null ||
      answer_correctness === null ||
      answer_faithfulness === null ||
      acceptAsGold === null
    ) {
      return
    }
    submitMutation.mutate({
      question_validity,
      evidence_quality,
      answer_correctness,
      answer_faithfulness,
      accept_as_gold: acceptAsGold,
      notes: notes.trim() || null,
    })
  }

  if (nextQuery.isLoading || payloadQuery.isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />
        Loading retrieval review
      </div>
    )
  }

  if (nextQuery.isError) {
    return (
      <div className="flex flex-col gap-3">
        <div className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
          {apiMessage(nextQuery.error)}
        </div>
      </div>
    )
  }

  if (assignmentId === null) {
    return (
      <div className="flex flex-col gap-3">
        <div className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
          No retrieval review tasks are available.
        </div>
      </div>
    )
  }

  if (payloadQuery.isError) {
    return (
      <div className="flex flex-col gap-3">
        <div className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
          {apiMessage(payloadQuery.error)}
        </div>
      </div>
    )
  }

  if (!payload) {
    return null
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="mx-auto grid w-full max-w-[100rem] gap-6 lg:grid-cols-[32rem_minmax(0,1fr)]">
        <aside className="space-y-5 lg:sticky lg:top-0 lg:max-h-[calc(100svh-8rem)] lg:self-start lg:overflow-y-auto lg:pr-1">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="text-muted-foreground text-sm">
                {payload.dataset.display_name}
              </p>
              <div className="mt-1 text-sm font-medium">
                {categoryLabel(payload.item.category)}
              </div>
            </div>
            <Badge variant="outline">
              {nextQuery.data?.reservation_state === "existing"
                ? "Existing assignment"
                : "New assignment"}
            </Badge>
          </div>

          {documents.length > 1 ? (
            <div className="space-y-2">
              <Label>Source Documents</Label>
              {documents.map((document) => (
                <button
                  className={cn(
                    "block w-full rounded-md border p-2 text-left text-sm",
                    activeDocument?.id === document.id &&
                      "border-primary/50 bg-primary/5",
                  )}
                  key={document.id}
                  onClick={() => setActiveDocumentId(document.id)}
                  type="button"
                >
                  {document.title}
                </button>
              ))}
            </div>
          ) : null}

          <section
            className="space-y-4 rounded-md border bg-background p-4 shadow-xs"
            id={reviewItemId ? evalItemDomId(reviewItemId) : undefined}
          >
            <div className="flex items-center justify-between gap-3">
              <h2 className="text-lg font-semibold">Eval Item</h2>
            </div>
            <button
              className="block w-full space-y-3 text-left"
              onClick={() => {
                const span =
                  payload.gold_evidence_spans?.[0] ??
                  payload.trap_evidence_spans?.[0]
                if (span) {
                  locateEvidenceSpan(span)
                }
              }}
              type="button"
            >
              <div className="space-y-1">
                <div className="text-sm font-medium">Question</div>
                <p className="whitespace-pre-wrap text-base">
                  {payload.item.prompt_text}
                </p>
              </div>
              {payload.item.expected_answer ? (
                <div className="space-y-1">
                  <div className="text-sm font-medium">Expected answer</div>
                  <p className="whitespace-pre-wrap text-base">
                    {payload.item.expected_answer}
                  </p>
                </div>
              ) : null}
              {payload.item.why_not_answerable ? (
                <div className="space-y-1">
                  <div className="text-sm font-medium">Why not answerable</div>
                  <p className="whitespace-pre-wrap text-base">
                    {payload.item.why_not_answerable}
                  </p>
                </div>
              ) : null}
            </button>

            <EvidenceList
              onLocate={locateEvidenceSpan}
              spans={payload.gold_evidence_spans}
              title="Evidence"
            />
            <EvidenceList
              onLocate={locateEvidenceSpan}
              spans={payload.trap_evidence_spans}
              title="Trap evidence"
            />
          </section>

          <section className="space-y-4 rounded-md border bg-background p-4 shadow-xs">
            <h2 className="text-lg font-semibold">Review</h2>
            <div className="space-y-4">
              {rubricFields.map((field) => (
                <RubricScoreField
                  definition={field}
                  key={field.key}
                  onChange={(value) => updateRubricValue(field.key, value)}
                  value={rubricValues[field.key]}
                />
              ))}
            </div>

            <div className="grid gap-2 sm:grid-cols-[1fr_auto] sm:items-center">
              <div className="min-w-0">
                <Label className="text-sm font-medium">Accept as gold?</Label>
                <p className="mt-1 text-xs text-muted-foreground">
                  Would you accept this example as a gold-standard reference?
                </p>
              </div>
              <div className="grid grid-cols-2 rounded-md border bg-background p-1">
                {[
                  { label: "Yes", value: true },
                  { label: "No", value: false },
                ].map((option) => (
                  <label className="block" key={option.label}>
                    <input
                      aria-label={option.label}
                      checked={acceptAsGold === option.value}
                      className="sr-only"
                      name="accept-as-gold"
                      onChange={() => setGoldDecision(option.value)}
                      type="radio"
                    />
                    <span
                      className={cn(
                        "flex h-8 min-w-16 cursor-pointer items-center justify-center rounded-sm px-3 text-sm font-medium transition",
                        acceptAsGold === option.value
                          ? "bg-primary text-primary-foreground shadow-xs"
                          : "text-muted-foreground hover:bg-muted hover:text-foreground",
                      )}
                    >
                      {option.label}
                    </span>
                  </label>
                ))}
              </div>
            </div>

            <div className="space-y-2">
              <Label htmlFor="review-notes">Notes</Label>
              <textarea
                className="border-input focus-visible:border-ring focus-visible:ring-ring/50 min-h-24 w-full rounded-md border bg-transparent px-3 py-2 text-sm shadow-xs outline-none focus-visible:ring-[3px]"
                id="review-notes"
                onChange={(event) => setNotes(event.target.value)}
                placeholder="Optional rationale, ambiguity, or reason for rejecting/fixing this item."
                value={notes}
              />
            </div>

            <RubricDetails />
          </section>

          {errorMessage ? (
            <div className="rounded-md border border-destructive/40 p-3 text-sm">
              {errorMessage}
            </div>
          ) : null}

          {completionMessage ? (
            <div className="rounded-md border p-3 text-sm">
              {completionMessage}
            </div>
          ) : null}

          <Button disabled={!canSubmit} onClick={submitReview} type="button">
            {submitMutation.isPending ? "Submitting" : "Submit review"}
          </Button>
        </aside>

        <section className="space-y-6">
          <RetrievalReviewDocumentViewer
            committedSpansByChunk={committedSpansByChunk}
            document={activeDocument}
            fallbackChunks={fallbackChunks}
            onCommittedSelect={scrollToReviewItem}
            selectionInstruction={<ReviewInstructions />}
            selectionMode={selectionMode}
          />
        </section>
      </div>
    </div>
  )
}
