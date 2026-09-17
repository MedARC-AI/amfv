import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
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
  type OwnedFactDecompItemDetail,
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
import { downloadLocalCopy } from "@/editorDownload"
import {
  type FactSaveCommandKind,
  factSaveReceiptMatchesCommand,
} from "@/factSaveRecovery"
import useAuth from "@/hooks/useAuth"
import { useEditorExit } from "@/hooks/useEditorExit"
import { useEditorLifetime } from "@/hooks/useEditorLifetime"
import { homeSummaryQueryKey } from "@/lib/queries"
import {
  apiErrorMessage,
  hasApiErrorStatus,
  isAuthoritativeClientError,
} from "@/utils"

export const Route = createFileRoute("/_layout/create/fact-decomposition")({
  component: FactDecompositionCreate,
  validateSearch: (search: Record<string, unknown>): { item_id?: number } => {
    const id = Number(search.item_id)
    return { item_id: Number.isSafeInteger(id) && id > 0 ? id : undefined }
  },
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

function newFactSaveRequestId(): string {
  return (
    globalThis.crypto?.randomUUID?.() ??
    `fact-save-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
  )
}

function newFact(polarity: FactDraft["polarity"]): FactDraft {
  return {
    fact_text: "",
    polarity,
    provenance_spans: [],
  }
}

function initialFacts(): FactDraft[] {
  return [newFact("SHOULD_LIST"), newFact("SHOULD_NOT_LIST")]
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
  return facts.map((fact) => ({
    ...fact,
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
  const { user } = useAuth()
  return user ? (
    <FactCreateEditor key={user.id} userId={user.id} />
  ) : (
    <p>Loading your account…</p>
  )
}

type PendingSave = { kind: FactSaveCommandKind; command: FactDecompSaveCommand }

function editorValue(
  datasetId: number | null,
  documentId: number | null,
  source: string,
  facts: FactDraft[],
) {
  return JSON.stringify({
    datasetId,
    documentId,
    source,
    facts: normalizeFacts(facts),
  })
}

function FactCreateEditor({ userId }: { userId: string }) {
  const queryClient = useQueryClient()
  const search = Route.useSearch()
  const navigate = Route.useNavigate()
  const { capture, invalidate } = useEditorLifetime(search.item_id)
  const [datasetId, setDatasetId] = React.useState<number | null>(null)
  const [activeDocumentId, setActiveDocumentId] = React.useState<number | null>(
    null,
  )
  const [sourceText, setSourceText] = React.useState("")
  const [facts, setFacts] = React.useState<FactDraft[]>(initialFacts)
  const [selectedFactIndex, setSelectedFactIndex] = React.useState<
    number | null
  >(0)
  const [validation, setValidation] = React.useState<ValidationPreview | null>(
    null,
  )
  const [resultMessage, setResultMessage] = React.useState<string | null>(null)
  const [draftIdentity, setDraftIdentity] =
    React.useState<DraftIdentity | null>(null)
  const [baseline, setBaseline] = React.useState(() =>
    editorValue(null, null, "", initialFacts()),
  )
  const [permission, setPermission] =
    React.useState<OwnedFactDecompItemDetail | null>(null)
  const [pending, setPending] = React.useState<PendingSave | null>(null)
  const [saveState, setSaveState] = React.useState<
    "idle" | "saving" | "uncertain" | "conflict"
  >("idle")
  const reloadOperation = React.useRef<{ itemId: number } | null>(null)
  const [reloading, setReloading] = React.useState(false)
  React.useEffect(() => {
    if (reloadOperation.current?.itemId !== search.item_id) {
      reloadOperation.current = null
      setReloading(false)
    }
  }, [search.item_id])
  const currentValue = editorValue(
    datasetId,
    activeDocumentId,
    sourceText,
    facts,
  )
  const contentVersion = React.useRef({ value: currentValue, version: 0 })
  if (contentVersion.current.value !== currentValue) {
    contentVersion.current = {
      value: currentValue,
      version: contentVersion.current.version + 1,
    }
  }
  const dirty = currentValue !== baseline
  const uncertain = saveState === "saving" || saveState === "uncertain"
  const locked =
    reloading ||
    saveState !== "idle" ||
    permission?.can_edit === false ||
    (draftIdentity !== null && draftIdentity.status !== "DRAFT")
  const displayingCurrentDraft =
    search.item_id === undefined || draftIdentity?.id === search.item_id
  const { dialog, confirmAction } = useEditorExit(
    displayingCurrentDraft && dirty,
    displayingCurrentDraft && uncertain,
  )
  const selectedFact = facts[selectedFactIndex ?? 0]

  const detailQuery = useQuery({
    queryKey: ["fact-draft", userId, search.item_id],
    queryFn: () =>
      CreateService.readOwnedFactDecompItem({
        itemId: search.item_id as number,
      }),
    enabled: search.item_id !== undefined,
    retry: false,
    refetchOnMount: "always",
  })
  const hydrate = React.useCallback((detail: OwnedFactDecompItemDetail) => {
    setDatasetId(detail.dataset_id)
    setActiveDocumentId(detail.document_id)
    setSourceText(detail.source_text)
    setFacts(normalizeFacts(detail.facts))
    setSelectedFactIndex(detail.facts.length ? 0 : null)
    setDraftIdentity({
      id: detail.id,
      revision: detail.item_revision,
      status: detail.status,
    })
    setPermission(detail)
    setBaseline(
      editorValue(
        detail.dataset_id,
        detail.document_id,
        detail.source_text,
        detail.facts,
      ),
    )
    setSaveState("idle")
    setPending(null)
    setValidation(null)
    setResultMessage(null)
  }, [])
  React.useEffect(() => {
    const detail = detailQuery.data
    if (
      !detail ||
      detail.id !== search.item_id ||
      detailQuery.isFetching ||
      reloading
    )
      return
    if (draftIdentity?.id !== detail.id) hydrate(detail)
    else if (detail.item_revision < draftIdentity.revision) return
    else if (
      draftIdentity.revision !== detail.item_revision ||
      permission?.can_edit !== detail.can_edit
    ) {
      if (dirty || pending) {
        setSaveState("conflict")
        setResultMessage(
          "The saved item changed in another tab. Download your local copy or reload the saved version.",
        )
      } else hydrate(detail)
    }
  }, [
    detailQuery.data,
    detailQuery.isFetching,
    reloading,
    search.item_id,
    draftIdentity,
    permission,
    dirty,
    pending,
    hydrate,
  ])

  const optionsQuery = useQuery({
    queryKey: ["create-options", userId],
    queryFn: CreateService.readCreateOptions,
  })
  const factDatasets =
    optionsQuery.data?.filter(
      (dataset) => dataset.eval_type === "FACT_DECOMP",
    ) ?? []
  const documentsQuery = useQuery({
    queryKey: ["create-source-documents", userId, datasetId, "FACT_DECOMP"],
    queryFn: () =>
      CreateService.readSourceDocuments({
        datasetId: datasetId as number,
        evalType: "FACT_DECOMP",
      }),
    enabled: datasetId !== null,
  })
  const activeDocumentQuery = useQuery({
    queryKey: [
      "create-source-document-detail",
      userId,
      datasetId,
      activeDocumentId,
    ],
    queryFn: () =>
      CreateService.readSourceDocumentDetail({
        datasetId: datasetId as number,
        documentId: activeDocumentId as number,
        evalType: "FACT_DECOMP",
      }),
    enabled:
      datasetId !== null &&
      activeDocumentId !== null &&
      permission?.can_edit !== false,
  })
  const clearMessage = () => {
    setValidation(null)
    setResultMessage(null)
  }
  const updateFacts = (nextFacts: FactDraft[]) => {
    if (locked) return
    setFacts(normalizeFacts(nextFacts))
    setSelectedFactIndex((index) =>
      nextFacts.length ? Math.min(index ?? 0, nextFacts.length - 1) : null,
    )
    clearMessage()
  }
  const requestBody = (): CreateFactDecompDraftSubmit => ({
    dataset_id: datasetId ?? 0,
    document_id: activeDocumentId,
    expected_item_revision: draftIdentity?.revision ?? null,
    item_id: draftIdentity?.id ?? null,
    source_text: sourceText,
    facts: normalizeFacts(facts),
  })
  const validateMutation = useMutation({
    mutationFn: ({
      body,
    }: {
      body: CreateFactDecompDraftSubmit
      isCurrent: () => boolean
    }) => CreateService.previewFactDecompCreation({ requestBody: body }),
    onSuccess: (result, { isCurrent }) => {
      if (isCurrent()) setValidation(result)
    },
    onError: (error, { isCurrent }) => {
      if (isCurrent()) setResultMessage(apiErrorMessage(error))
    },
  })
  const applySaved = async (
    captured: PendingSave,
    response: FactDecompCreateResponse,
    recovered: boolean,
    isCurrent: () => boolean,
  ) => {
    if (!isCurrent()) return
    // A receipt establishes a commit, not that this revision is still current.
    const latest = await CreateService.readOwnedFactDecompItem({
      itemId: response.id,
    })
    if (!isCurrent()) return
    if (
      latest.item_revision !== response.item_revision ||
      latest.status !== response.status
    ) {
      setSaveState("conflict")
      setResultMessage(
        "This save committed, but another tab has since changed the item. Download your local copy or reload the latest saved version.",
      )
      setDraftIdentity({
        id: response.id,
        revision: response.item_revision,
        status: response.status,
      })
      await navigate({
        replace: true,
        search: { item_id: response.id },
        ignoreBlocker: true,
      })
      return
    }
    await queryClient.cancelQueries({
      queryKey: ["fact-draft", userId, response.id],
      exact: true,
    })
    if (!isCurrent()) return
    hydrate(latest)
    queryClient.setQueryData(["fact-draft", userId, response.id], latest)
    setValidation(response.validation)
    setResultMessage(
      captured.kind === "draft"
        ? recovered
          ? `Draft ${response.id} was recovered at revision ${response.item_revision}.`
          : `Draft saved as item ${response.id} (revision ${response.item_revision}).`
        : recovered
          ? `Submission of item ${response.id} was recovered.`
          : `Submitted item ${response.id}.`,
    )
    void queryClient.invalidateQueries({ queryKey: homeSummaryQueryKey })
    void queryClient.invalidateQueries({ queryKey: ["fact-drafts", userId] })
    await navigate({
      replace: true,
      search: { item_id: response.id },
      ignoreBlocker: true,
    })
  }
  const reconcile = async (captured: PendingSave, isCurrent = capture()) => {
    if (!isCurrent()) return
    setSaveState("saving")
    try {
      const receipt = await CreateService.readFactDecompSaveReceipt({
        requestId: captured.command.request_id,
      })
      if (!isCurrent()) return
      if (
        !factSaveReceiptMatchesCommand(receipt, captured.command, captured.kind)
      ) {
        setSaveState("conflict")
        setResultMessage(
          "The save receipt does not match this command. Download your local copy; this response cannot confirm your save.",
        )
        return
      }
      await applySaved(captured, receipt.response, true, isCurrent)
    } catch {
      if (!isCurrent()) return
      setSaveState("uncertain")
      setResultMessage(
        "Save outcome is unknown. Check save status, retry the same save, or download your local copy. A missing receipt does not prove that the save failed.",
      )
    }
  }
  const send = async (captured: PendingSave) => {
    const isCurrent = capture()
    setPending(captured)
    setSaveState("saving")
    setResultMessage(null)
    let response: FactDecompCreateResponse
    try {
      response = await (captured.kind === "draft"
        ? CreateService.createFactDecompDraft
        : CreateService.submitFactDecompDraft)({
        requestBody: captured.command,
      })
    } catch (error) {
      if (!isCurrent()) return
      if (hasApiErrorStatus(error, 409)) {
        setSaveState("conflict")
        setResultMessage(
          `${apiErrorMessage(error)} Download your local copy or reload the saved version.`,
        )
      } else if (isAuthoritativeClientError(error)) {
        setSaveState("idle")
        setPending(null)
        setResultMessage(apiErrorMessage(error))
      } else await reconcile(captured, isCurrent)
      return
    }
    try {
      await applySaved(captured, response, false, isCurrent)
    } catch {
      await reconcile(captured, isCurrent)
    }
  }
  const save = (kind: FactSaveCommandKind) => {
    if (locked) return
    void send({
      kind,
      command: { ...requestBody(), request_id: newFactSaveRequestId() },
    })
  }
  const resetDataset = React.useCallback(
    (nextDatasetId: number | null) => {
      invalidate()
      reloadOperation.current = null
      setReloading(false)
      setDatasetId(nextDatasetId)
      setActiveDocumentId(null)
      setSourceText("")
      setFacts(initialFacts())
      setSelectedFactIndex(0)
      setValidation(null)
      setResultMessage(null)
      setDraftIdentity(null)
      setPermission(null)
      setPending(null)
      setSaveState("idle")
      setBaseline(editorValue(nextDatasetId, null, "", initialFacts()))
    },
    [invalidate],
  )
  const previousItemId = React.useRef(search.item_id)
  React.useEffect(() => {
    if (search.item_id === undefined && previousItemId.current !== undefined)
      resetDataset(null)
    previousItemId.current = search.item_id
  }, [search.item_id, resetDataset])
  const selectDocument = (value: string) => {
    if (locked) return
    confirmAction(() => {
      setActiveDocumentId(value === noDocumentValue ? null : Number(value))
      setFacts((current) =>
        current.map((fact) => ({ ...fact, provenance_spans: [] })),
      )
      clearMessage()
    })
  }
  const useDocumentText = () => {
    if (locked) return
    const replace = () => {
      setSourceText(activeDocumentQuery.data?.content ?? "")
      clearMessage()
    }
    if (sourceText) confirmAction(replace)
    else replace()
  }
  const addProvenanceSpan = (span: EvidenceSpan) => {
    if (locked || selectedFactIndex === null || !selectedFact) return
    updateFacts(
      facts.map((fact, index) =>
        index === selectedFactIndex
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
    if (locked || selectedFactIndex === null || !selectedFact) return
    updateFacts(
      facts.map((fact, index) =>
        index === selectedFactIndex
          ? { ...fact, provenance_spans: nextSpans }
          : fact,
      ),
    )
  }
  const isBusy =
    (validateMutation.isPending && validateMutation.variables?.isCurrent()) ||
    saveState === "saving"
  const reloadSaved = () => {
    if (reloadOperation.current) return
    confirmAction(() => {
      if (reloadOperation.current) return
      const id = draftIdentity?.id ?? search.item_id
      if (!id) return
      const operation = { itemId: id }
      reloadOperation.current = operation
      setReloading(true)
      const isCurrent = capture()
      const version = contentVersion.current.version
      void (async () => {
        try {
          await queryClient.cancelQueries({
            queryKey: ["fact-draft", userId, id],
            exact: true,
          })
          const detail = await CreateService.readOwnedFactDecompItem({
            itemId: id,
          })
          if (
            !isCurrent() ||
            reloadOperation.current !== operation ||
            contentVersion.current.version !== version
          )
            return
          if (detail.item_revision < (draftIdentity?.revision ?? 0)) return
          queryClient.setQueryData(["fact-draft", userId, id], detail)
          hydrate(detail)
        } catch (error) {
          if (isCurrent() && contentVersion.current.version === version)
            setResultMessage(apiErrorMessage(error))
        } finally {
          if (reloadOperation.current === operation) {
            reloadOperation.current = null
            setReloading(false)
          }
        }
      })()
    })
  }
  if (search.item_id !== undefined && draftIdentity?.id !== search.item_id) {
    return detailQuery.isError ? (
      <div>
        <p>{apiErrorMessage(detailQuery.error)}</p>
        <Button onClick={() => void detailQuery.refetch()}>
          Retry loading draft
        </Button>
        {dialog}
      </div>
    ) : (
      <div>
        <p>Loading saved draft…</p>
        {dialog}
      </div>
    )
  }
  return (
    <div className="flex flex-col gap-6">
      {dialog}
      {draftIdentity ? (
        <div>
          Item {draftIdentity.id} · Revision {draftIdentity.revision} ·{" "}
          {draftIdentity.status} · {permission?.dataset_name}
        </div>
      ) : null}
      {permission?.can_edit === false ? (
        <p>
          This saved item is read-only (
          {permission.read_only_reason?.replace(/_/g, " ")}).
        </p>
      ) : null}
      <Button
        variant="outline"
        className="self-start"
        onClick={() =>
          confirmAction(() => {
            if (search.item_id === undefined) resetDataset(null)
            else void navigate({ search: {}, ignoreBlocker: true })
          })
        }
      >
        Create new draft
      </Button>
      {saveState === "uncertain" || saveState === "conflict" ? (
        <div className="flex flex-wrap gap-2">
          {saveState === "uncertain" && pending ? (
            <>
              <Button onClick={() => void reconcile(pending)}>
                Check save status
              </Button>
              <Button onClick={() => void send(pending)}>
                Retry same save
              </Button>
            </>
          ) : null}
          {draftIdentity && saveState === "conflict" ? (
            <Button disabled={reloading} onClick={reloadSaved}>
              Reload saved version
            </Button>
          ) : null}
          <Button
            variant="outline"
            onClick={() =>
              downloadLocalCopy("fact-draft.json", {
                ...requestBody(),
                pending,
              })
            }
          >
            Download local copy
          </Button>
        </div>
      ) : null}
      <div className="grid min-w-0 grid-cols-1 gap-6 lg:grid-cols-[20rem_minmax(0,1fr)]">
        <aside className="min-w-0 space-y-5">
          <div className="space-y-2">
            <Label>Dataset</Label>
            <Select
              disabled={locked || draftIdentity !== null}
              onValueChange={(value) =>
                confirmAction(() => resetDataset(Number(value)))
              }
              value={datasetId?.toString() ?? ""}
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
              disabled={locked || datasetId === null}
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
              onValueChange={(value) => setSelectedFactIndex(Number(value))}
              value={selectedFactIndex?.toString() ?? ""}
            >
              <SelectTrigger className="w-full" data-testid="fact-select">
                <SelectValue placeholder="Select fact for provenance" />
              </SelectTrigger>
              <SelectContent>
                {facts.map((fact, index) => (
                  <SelectItem key={index} value={index.toString()}>
                    {`Fact ${index + 1}: ${fact.fact_text || "Untitled"}`}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </aside>

        <section className="min-w-0 space-y-6">
          <div className="space-y-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <Label htmlFor="source-text">Source text</Label>
              <Button
                disabled={locked || !activeDocumentQuery.data}
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
              disabled={locked}
              id="source-text"
              onChange={(event) => {
                setSourceText(event.target.value)
                setValidation(null)
                setResultMessage(null)
              }}
              value={sourceText}
            />
          </div>

          <FactListEditor
            disabled={locked}
            facts={facts}
            onChange={updateFacts}
            onSelectedFactIndexChange={setSelectedFactIndex}
            selectedFactIndex={selectedFactIndex}
          />

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
                  readOnly={locked}
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
            disabled={locked}
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
                ? `Provenance for fact ${(selectedFactIndex ?? 0) + 1}`
                : "Provenance"
            }
          />

          <ValidationMessages flags={validation?.flags} ok={validation?.ok} />

          {resultMessage ? (
            <div className="rounded-md border p-3 text-sm">{resultMessage}</div>
          ) : null}

          <div className="flex flex-wrap gap-2">
            <Button
              disabled={datasetId === null || isBusy || locked}
              onClick={() => {
                const isCurrent = capture()
                const version = contentVersion.current.version
                validateMutation.mutate({
                  body: requestBody(),
                  isCurrent: () =>
                    isCurrent() && contentVersion.current.version === version,
                })
              }}
              type="button"
              variant="outline"
            >
              Validate
            </Button>
            <Button
              disabled={datasetId === null || isBusy || locked}
              onClick={() => save("draft")}
              type="button"
              variant="outline"
            >
              Save draft
            </Button>
            <Button
              disabled={
                datasetId === null || isBusy || locked || draftIdentity === null
              }
              onClick={() => save("submit")}
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
