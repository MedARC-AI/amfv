import { useMutation, useQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { Loader2 } from "lucide-react"
import * as React from "react"

import {
  type CreateFactDecompDraftSubmit,
  CreateService,
  type EvidenceSpan,
  type FactDecompCreateResponse,
  type FactDecompSaveCommand,
  type FactDraft,
  type ValidationPreview,
} from "@/client"
import {
  EvidenceTray,
  type EvidenceTraySpan,
} from "@/components/annotation/EvidenceTray"
import { FactListEditor } from "@/components/annotation/FactListEditor"
import { SelectableChunk } from "@/components/annotation/SelectableChunk"
import { ValidationMessages } from "@/components/annotation/ValidationMessages"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  type FactSaveCommandKind,
  factSaveReceiptMatchesCommand,
} from "@/factSaveRecovery"
import {
  apiErrorMessage,
  hasApiErrorStatus,
  isAuthoritativeClientError,
  ProductMessageError,
} from "@/utils"

export const Route = createFileRoute("/_layout/create/fact-decomposition")({
  component: FactDecompositionCreate,
  head: () => ({
    meta: [
      {
        title: "Create Fact Decomposition - AMFV Web",
      },
    ],
  }),
})

const noDocumentValue = "none"

type DraftIdentity = {
  id: number
  revision: number
  status: FactDecompCreateResponse["status"]
}

type FactMutationResult = {
  reconciled: boolean
  response: FactDecompCreateResponse
}

class UncertainFactSaveError extends ProductMessageError {}

function newFactSaveRequestId(): string {
  return (
    globalThis.crypto?.randomUUID?.() ??
    `fact-save-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
  )
}

function uncertainFactSaveMessage(command: FactSaveCommandKind): string {
  return command === "draft"
    ? "Draft save outcome is unknown. It was not retried automatically; do not assume the draft was saved."
    : "Submission outcome is unknown. It was not retried automatically; do not assume the item was submitted."
}

function newFact(position: number, polarity: FactDraft["polarity"]): FactDraft {
  return {
    fact_uuid:
      globalThis.crypto?.randomUUID?.() ??
      `fact-${Date.now().toString(36)}-${position}`,
    fact_text: "",
    polarity,
    position,
    provenance_spans: [],
  }
}

function initialFacts(): FactDraft[] {
  return [newFact(0, "SHOULD_LIST"), newFact(1, "SHOULD_NOT_LIST")]
}

function evidenceForSubmit(spans: EvidenceSpan[] | undefined): EvidenceSpan[] {
  return (spans ?? []).map(({ chunk_id, start, end, text }) => ({
    chunk_id,
    start,
    end,
    text,
  }))
}

function normalizeFacts(facts: FactDraft[]): FactDraft[] {
  return [...facts]
    .sort((left, right) => left.position - right.position)
    .map((fact, position) => ({
      ...fact,
      position,
      provenance_spans: evidenceForSubmit(fact.provenance_spans),
    }))
}

function moveSpan(
  spans: EvidenceSpan[],
  index: number,
  direction: "up" | "down",
): EvidenceSpan[] {
  const targetIndex = direction === "up" ? index - 1 : index + 1
  if (index < 0 || targetIndex < 0 || targetIndex >= spans.length) {
    return spans
  }
  const nextSpans = [...spans]
  const [span] = nextSpans.splice(index, 1)
  nextSpans.splice(targetIndex, 0, span)
  return nextSpans
}

function factEvidenceTraySpans(
  fact: FactDraft | undefined,
): EvidenceTraySpan[] {
  return (fact?.provenance_spans ?? []).map((span, index) => ({
    ...span,
    id: index.toString(),
    label: `Chunk ${span.chunk_id}`,
  }))
}

function FactDecompositionCreate() {
  const [datasetId, setDatasetId] = React.useState<number | null>(null)
  const [activeDocumentId, setActiveDocumentId] = React.useState<number | null>(
    null,
  )
  const [sourceText, setSourceText] = React.useState("")
  const [facts, setFacts] = React.useState<FactDraft[]>(() => initialFacts())
  const [selectedFactUuid, setSelectedFactUuid] = React.useState<string | null>(
    null,
  )
  const [validation, setValidation] = React.useState<ValidationPreview | null>(
    null,
  )
  const [resultMessage, setResultMessage] = React.useState<string | null>(null)
  const [draftIdentity, setDraftIdentity] =
    React.useState<DraftIdentity | null>(null)
  const [writeOutcomeUnknown, setWriteOutcomeUnknown] = React.useState(false)

  const selectedFact =
    facts.find((fact) => fact.fact_uuid === selectedFactUuid) ?? facts[0]

  React.useEffect(() => {
    if (!selectedFactUuid && facts[0]) {
      setSelectedFactUuid(facts[0].fact_uuid)
    } else if (
      selectedFactUuid &&
      !facts.some((fact) => fact.fact_uuid === selectedFactUuid)
    ) {
      setSelectedFactUuid(facts[0]?.fact_uuid ?? null)
    }
  }, [facts, selectedFactUuid])

  const optionsQuery = useQuery({
    queryKey: ["create-options"],
    queryFn: CreateService.readCreateOptions,
  })

  const factDatasets =
    optionsQuery.data?.filter(
      (dataset) => dataset.eval_type === "FACT_DECOMP",
    ) ?? []

  const documentsQuery = useQuery({
    queryKey: ["create-source-documents", datasetId, "FACT_DECOMP"],
    queryFn: () =>
      CreateService.readSourceDocuments({
        datasetId: datasetId as number,
        evalType: "FACT_DECOMP",
      }),
    enabled: datasetId !== null,
  })

  const activeDocumentQuery = useQuery({
    queryKey: ["create-source-document-detail", datasetId, activeDocumentId],
    queryFn: () =>
      CreateService.readSourceDocumentDetail({
        datasetId: datasetId as number,
        documentId: activeDocumentId as number,
        evalType: "FACT_DECOMP",
      }),
    enabled: datasetId !== null && activeDocumentId !== null,
  })

  const updateFacts = (nextFacts: FactDraft[]) => {
    setFacts(normalizeFacts(nextFacts))
    setValidation(null)
    setResultMessage(null)
  }

  const requestBody = (): CreateFactDecompDraftSubmit => ({
    dataset_id: datasetId ?? 0,
    document_id: activeDocumentId,
    expected_item_revision: draftIdentity?.revision ?? null,
    item_id: draftIdentity?.id ?? null,
    source_text: sourceText,
    facts: normalizeFacts(facts),
  })

  const saveCommand = (): FactDecompSaveCommand => ({
    ...requestBody(),
    request_id: newFactSaveRequestId(),
  })

  const reconcileFactSave = async (
    command: FactDecompSaveCommand,
    commandKind: FactSaveCommandKind,
  ): Promise<FactDecompCreateResponse> => {
    try {
      const receipt = await CreateService.readFactDecompSaveReceipt({
        requestId: command.request_id,
      })
      if (!factSaveReceiptMatchesCommand(receipt, command, commandKind)) {
        throw new UncertainFactSaveError(uncertainFactSaveMessage(commandKind))
      }
      return receipt.response
    } catch (error) {
      if (error instanceof UncertainFactSaveError) {
        throw error
      }
      if (hasApiErrorStatus(error, 404)) {
        throw new UncertainFactSaveError(uncertainFactSaveMessage(commandKind))
      }
      throw new UncertainFactSaveError(uncertainFactSaveMessage(commandKind))
    }
  }

  const validateMutation = useMutation({
    mutationFn: () =>
      CreateService.previewFactDecompCreation({
        requestBody: requestBody(),
      }),
    onSuccess: setValidation,
    onError: (error) => setResultMessage(apiErrorMessage(error)),
  })

  const draftMutation = useMutation({
    mutationFn: async (): Promise<FactMutationResult> => {
      const command = saveCommand()
      try {
        const response = await CreateService.createFactDecompDraft({
          requestBody: command,
        })
        return { response, reconciled: false }
      } catch (error) {
        if (isAuthoritativeClientError(error)) {
          throw error
        }
        const response = await reconcileFactSave(command, "draft")
        return { response, reconciled: true }
      }
    },
    onSuccess: ({ response, reconciled }) => {
      setWriteOutcomeUnknown(false)
      setDraftIdentity({
        id: response.id,
        revision: response.item_revision,
        status: response.status,
      })
      setValidation(response.validation)
      setResultMessage(
        reconciled
          ? `Draft ${response.id} was recovered at revision ${response.item_revision}.`
          : `Draft saved as item ${response.id} (revision ${response.item_revision}).`,
      )
    },
    onError: (error) => {
      if (error instanceof UncertainFactSaveError) {
        setWriteOutcomeUnknown(true)
      }
      setResultMessage(apiErrorMessage(error))
    },
  })

  const submitMutation = useMutation({
    mutationFn: async (): Promise<FactMutationResult> => {
      if (!draftIdentity) {
        throw new ProductMessageError(
          "Save this draft before submitting it for moderation.",
        )
      }
      const command = saveCommand()
      try {
        const response = await CreateService.submitFactDecompDraft({
          requestBody: command,
        })
        return { response, reconciled: false }
      } catch (error) {
        if (isAuthoritativeClientError(error)) {
          throw error
        }
        const response = await reconcileFactSave(command, "submit")
        return { response, reconciled: true }
      }
    },
    onSuccess: ({ response, reconciled }) => {
      setWriteOutcomeUnknown(false)
      setDraftIdentity({
        id: response.id,
        revision: response.item_revision,
        status: response.status,
      })
      setValidation(response.validation)
      setResultMessage(
        reconciled
          ? `Submission of item ${response.id} was recovered.`
          : `Submitted item ${response.id}.`,
      )
    },
    onError: (error) => {
      if (error instanceof UncertainFactSaveError) {
        setWriteOutcomeUnknown(true)
      }
      setResultMessage(apiErrorMessage(error))
    },
  })

  const resetDataset = (nextDatasetId: number) => {
    setDatasetId(nextDatasetId)
    setActiveDocumentId(null)
    setSourceText("")
    setFacts(initialFacts())
    setSelectedFactUuid(null)
    setValidation(null)
    setResultMessage(null)
    setDraftIdentity(null)
    setWriteOutcomeUnknown(false)
  }

  const selectDocument = (value: string) => {
    setActiveDocumentId(value === noDocumentValue ? null : Number(value))
    setFacts((currentFacts) =>
      currentFacts.map((fact) => ({ ...fact, provenance_spans: [] })),
    )
    setValidation(null)
    setResultMessage(null)
  }

  const useDocumentText = () => {
    setSourceText(activeDocumentQuery.data?.content ?? "")
    setValidation(null)
    setResultMessage(null)
  }

  const addProvenanceSpan = (span: EvidenceSpan) => {
    if (!selectedFact) {
      return
    }
    updateFacts(
      facts.map((fact) =>
        fact.fact_uuid === selectedFact.fact_uuid
          ? {
              ...fact,
              provenance_spans: [
                ...(fact.provenance_spans ?? []),
                evidenceForSubmit([span])[0],
              ],
            }
          : fact,
      ),
    )
  }

  const selectedChunkSpans = facts.flatMap(
    (fact) => fact.provenance_spans ?? [],
  )

  const updateSelectedFactSpans = (nextSpans: EvidenceSpan[]) => {
    if (!selectedFact) {
      return
    }
    updateFacts(
      facts.map((fact) =>
        fact.fact_uuid === selectedFact.fact_uuid
          ? { ...fact, provenance_spans: nextSpans }
          : fact,
      ),
    )
  }

  const isBusy =
    validateMutation.isPending ||
    draftMutation.isPending ||
    submitMutation.isPending
  const isSubmitted = draftIdentity?.status === "SUBMITTED"

  return (
    <div className="flex flex-col gap-6">
      <div className="grid gap-6 lg:grid-cols-[20rem_1fr]">
        <aside className="space-y-5">
          <div className="space-y-2">
            <Label>Dataset</Label>
            <Select
              onValueChange={(value) => resetDataset(Number(value))}
              value={datasetId?.toString()}
            >
              <SelectTrigger className="w-full" data-testid="dataset-select">
                <SelectValue placeholder="Select fact dataset" />
              </SelectTrigger>
              <SelectContent>
                {factDatasets.map((dataset) => (
                  <SelectItem key={dataset.id} value={dataset.id.toString()}>
                    {dataset.display_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <Label>Source document</Label>
            <Select
              disabled={datasetId === null}
              onValueChange={selectDocument}
              value={activeDocumentId?.toString() ?? noDocumentValue}
            >
              <SelectTrigger className="w-full" data-testid="document-select">
                <SelectValue placeholder="Optional source document" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={noDocumentValue}>
                  No source document
                </SelectItem>
                {(documentsQuery.data ?? []).map((document) => (
                  <SelectItem key={document.id} value={document.id.toString()}>
                    {document.title}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <Label>Selected fact</Label>
            <Select
              disabled={facts.length === 0}
              onValueChange={setSelectedFactUuid}
              value={selectedFact?.fact_uuid}
            >
              <SelectTrigger className="w-full" data-testid="fact-select">
                <SelectValue placeholder="Select fact for provenance" />
              </SelectTrigger>
              <SelectContent>
                {facts.map((fact) => (
                  <SelectItem key={fact.fact_uuid} value={fact.fact_uuid}>
                    {`Fact ${fact.position + 1}: ${
                      fact.fact_text || "Untitled"
                    }`}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </aside>

        <section className="space-y-6">
          <div className="space-y-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <Label htmlFor="source-text">Source text</Label>
              <Button
                disabled={!activeDocumentQuery.data}
                onClick={useDocumentText}
                size="sm"
                type="button"
                variant="outline"
              >
                Use document text
              </Button>
            </div>
            <textarea
              className="border-input focus-visible:border-ring focus-visible:ring-ring/50 min-h-36 w-full rounded-md border bg-transparent px-3 py-2 text-sm shadow-xs outline-none focus-visible:ring-[3px]"
              id="source-text"
              onChange={(event) => {
                setSourceText(event.target.value)
                setValidation(null)
                setResultMessage(null)
              }}
              value={sourceText}
            />
          </div>

          <FactListEditor facts={facts} onChange={updateFacts} />

          {activeDocumentQuery.isLoading ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" />
              Loading source document
            </div>
          ) : activeDocumentQuery.data ? (
            <div className="space-y-3">
              <div className="text-sm font-medium">
                {activeDocumentQuery.data.title}
              </div>
              {(activeDocumentQuery.data.chunks ?? []).map((chunk) => (
                <SelectableChunk
                  chunk={chunk}
                  key={chunk.id}
                  onSelect={(span) => {
                    addProvenanceSpan(span)
                    return true
                  }}
                  selectedSpans={selectedChunkSpans.filter(
                    (span) => span.chunk_id === chunk.id,
                  )}
                />
              ))}
            </div>
          ) : (
            <div className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
              Select a source document to highlight provenance, or paste source
              text directly.
            </div>
          )}

          <EvidenceTray
            emptyLabel="Highlight source text to attach provenance to the selected fact"
            onMove={(id, direction) => {
              const index = Number(id)
              updateSelectedFactSpans(
                moveSpan(
                  selectedFact?.provenance_spans ?? [],
                  index,
                  direction,
                ),
              )
            }}
            onRemove={(id) => {
              const index = Number(id)
              updateSelectedFactSpans(
                (selectedFact?.provenance_spans ?? []).filter(
                  (_span, spanIndex) => spanIndex !== index,
                ),
              )
            }}
            spans={factEvidenceTraySpans(selectedFact)}
            title={
              selectedFact
                ? `Provenance for fact ${selectedFact.position + 1}`
                : "Provenance"
            }
          />

          <ValidationMessages flags={validation?.flags} ok={validation?.ok} />

          {resultMessage ? (
            <div className="rounded-md border p-3 text-sm">{resultMessage}</div>
          ) : null}

          <div className="flex flex-wrap gap-2">
            <Button
              disabled={datasetId === null || isBusy}
              onClick={() => validateMutation.mutate()}
              type="button"
              variant="outline"
            >
              Validate
            </Button>
            <Button
              disabled={
                datasetId === null ||
                isBusy ||
                isSubmitted ||
                writeOutcomeUnknown
              }
              onClick={() => draftMutation.mutate()}
              type="button"
              variant="outline"
            >
              Save draft
            </Button>
            <Button
              disabled={
                datasetId === null ||
                isBusy ||
                isSubmitted ||
                writeOutcomeUnknown ||
                draftIdentity === null
              }
              onClick={() => submitMutation.mutate()}
              type="button"
            >
              Submit
            </Button>
          </div>
        </section>
      </div>
    </div>
  )
}
