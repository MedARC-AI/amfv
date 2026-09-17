import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import * as React from "react"

import {
  type AuthoredFactDecompReviewPayload,
  type FactDecompReviewSubmit,
  type JudgmentConfidence,
  type ModelEvalReviewSubmit,
  type ModelFactDecompReviewPayload,
  type ReviewFact,
  type ReviewRubricDimension,
  ReviewService,
} from "@/client"
import {
  type CorrectionInputClaim,
  FactDecompositionCorrection,
} from "@/components/annotation/FactDecompositionCorrection"
import { GradingInstructions } from "@/components/annotation/GradingInstructions"
import { Badge } from "@/components/ui/badge"
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
import { valuesMatch } from "@/factSaveRecovery"
import useAuth from "@/hooks/useAuth"
import { useEditorExit } from "@/hooks/useEditorExit"
import { useEditorLifetime } from "@/hooks/useEditorLifetime"
import { useFactReviewHistory } from "@/hooks/useFactReviewHistory"
import { homeSummaryQueryKey } from "@/lib/queries"
import { isReviewActionAllowed } from "@/reviewCapabilities"
import {
  apiErrorMessage,
  hasApiErrorStatus,
  isAuthoritativeClientError,
} from "@/utils"

export const Route = createFileRoute("/_layout/review/fact-decomposition")({
  component: FactDecompositionReview,
  validateSearch: (search: Record<string, unknown>): { task_id?: number } => {
    const value = Number(search.task_id)
    return {
      task_id: Number.isInteger(value) && value > 0 ? value : undefined,
    }
  },
  head: () => ({
    meta: [
      {
        title: "Fact Decomposition Review - AMFV Web",
      },
    ],
  }),
})

const factCallOptions = [
  { value: "SHOULD_LIST", label: "Should list" },
  { value: "SHOULD_NOT_LIST", label: "Should not list" },
  { value: "MALFORMED", label: "Malformed" },
]

const confidences: Array<{ value: JudgmentConfidence; label: string }> = [
  { value: "EASY_CALL", label: "Easy call" },
  { value: "DELIBERATED", label: "Deliberated" },
]

function optionLabel(value: string): string {
  return value
    .split("_")
    .map((piece) => piece.charAt(0).toUpperCase() + piece.slice(1))
    .join(" ")
}

function initialFactCalls(facts: ReviewFact[] | undefined): string[] {
  return (facts ?? []).map((fact) => fact.polarity)
}

function initialRubricValues(
  dimensions: ReviewRubricDimension[] | undefined,
): Record<string, string> {
  return Object.fromEntries(
    (dimensions ?? []).map((dimension) => [
      dimension.key,
      dimension.options?.[0] ?? "pass",
    ]),
  )
}

function correctionClaims(
  payload: ModelFactDecompReviewPayload,
): CorrectionInputClaim[] {
  return payload.claims.map((entry) => ({
    claim_text: entry.claim_text,
    position: entry.position,
    response_spans: entry.response_spans,
    proposed_label: entry.proposed_label,
  }))
}

async function readNextFactReview() {
  try {
    return await ReviewService.readNextReviewTask({
      evalType: "FACT_DECOMP",
      mode: "ITEM_AUDIT",
    })
  } catch (error) {
    if (hasApiErrorStatus(error, 404)) return null
    throw error
  }
}

function FactDecompositionReview() {
  const { user } = useAuth()
  return user ? (
    <FactReviewEditor key={user.id} userId={user.id} />
  ) : (
    <p>Loading your account…</p>
  )
}

type PendingReview = {
  taskId: number
  userId: string
  mode: "AUTHORED_RUBRIC" | "MODEL_LABEL_CORRECTION"
  body: FactDecompReviewSubmit | ModelEvalReviewSubmit
}

function savedReviewMatches(
  payload: AuthoredFactDecompReviewPayload | ModelFactDecompReviewPayload,
  pending: PendingReview,
) {
  if (!payload.existing_review || payload.review_mode !== pending.mode)
    return false
  if (payload.review_mode === "MODEL_LABEL_CORRECTION") {
    const { item_revision: _revision, ...submission } =
      pending.body as ModelEvalReviewSubmit
    return valuesMatch(payload.existing_review, submission)
  }
  const saved = payload.existing_review as Record<string, unknown>
  const body = pending.body as FactDecompReviewSubmit
  const expectedRatings = {
    ...body.values,
    fact_calls: body.fact_calls,
    duplicate_flags: body.duplicate_flags,
    multiple_facts_flags: body.multiple_facts_flags,
    looks_good: body.looks_good,
  }
  const ratings = saved.ratings as Record<string, unknown>
  return (
    Object.entries(expectedRatings).every(([key, value]) =>
      valuesMatch(ratings[key], value),
    ) &&
    saved.item_revision === body.item_revision &&
    (saved.comment ?? null) === (body.comments ?? null) &&
    valuesMatch(saved.flags, { confidence: body.confidence ?? null })
  )
}

function FactReviewEditor({ userId }: { userId: string }) {
  const queryClient = useQueryClient()
  const { visited, historyUnavailable, visit } = useFactReviewHistory(userId)
  const search = Route.useSearch()
  const navigate = Route.useNavigate()
  const [multipleFactsFlags, setMultipleFactsFlags] = React.useState<boolean[]>(
    [],
  )
  const [duplicateFlags, setDuplicateFlags] = React.useState<boolean[]>([])
  const [looksGood, setLooksGood] = React.useState<boolean[]>([])
  const [factCalls, setFactCalls] = React.useState<string[]>([])
  const [rubricValues, setRubricValues] = React.useState<
    Record<string, string>
  >({})
  const [confidence, setConfidence] =
    React.useState<JudgmentConfidence>("EASY_CALL")
  const [comments, setComments] = React.useState("")
  const [completionMessage, setCompletionMessage] = React.useState<
    string | null
  >(null)
  const [errorMessage, setErrorMessage] = React.useState<string | null>(null)
  const [modelDirty, setModelDirty] = React.useState(false)
  const [pending, setPending] = React.useState<PendingReview | null>(null)
  const [recovery, setRecovery] = React.useState<
    "idle" | "saving" | "uncertain" | "retry" | "different"
  >("idle")
  const [baseline, setBaseline] = React.useState("")
  const initializedTask = React.useRef("")
  const nextKey = ["review-next", "fact-decomp", userId]
  const nextQuery = useQuery({
    queryKey: nextKey,
    queryFn: readNextFactReview,
    retry: false,
    enabled: search.task_id === undefined,
    refetchOnMount: "always",
    refetchOnWindowFocus: false,
  })
  const waitingForNext =
    search.task_id === undefined &&
    (!nextQuery.isFetchedAfterMount || nextQuery.isFetching)
  const taskId =
    search.task_id ??
    (waitingForNext || nextQuery.isError ? null : nextQuery.data?.task_id) ??
    null
  const { capture } = useEditorLifetime(taskId)
  const payloadKey = ["review-fact-decomp", userId, taskId]
  const payloadQuery = useQuery({
    queryKey: payloadKey,
    queryFn: () =>
      ReviewService.readFactDecompReview({ taskId: taskId as number }),
    enabled: taskId !== null,
    retry: false,
    refetchOnWindowFocus: false,
  })
  const [displayedPayload, setDisplayedPayload] = React.useState<
    AuthoredFactDecompReviewPayload | ModelFactDecompReviewPayload
  >()
  const payload =
    displayedPayload?.task_id === taskId ? displayedPayload : payloadQuery.data
  const modelSnapshot = React.useRef<string>("{}")
  const captureModelSnapshot = React.useCallback((snapshot: string) => {
    modelSnapshot.current = snapshot
  }, [])
  const saved = !!completionMessage || !!payload?.existing_review
  const authoredValue = JSON.stringify({
    factCalls,
    duplicateFlags,
    multipleFactsFlags,
    looksGood,
    rubricValues,
    comments,
    confidence,
  })
  const dirty =
    initializedTask.current.split(":")[0] === String(taskId) &&
    !saved &&
    (payload?.review_mode === "MODEL_LABEL_CORRECTION"
      ? modelDirty
      : !!baseline && authoredValue !== baseline)
  const uncertain =
    pending?.taskId === taskId &&
    (recovery === "saving" || recovery === "uncertain")
  const { dialog, confirmAction } = useEditorExit(dirty, uncertain)
  const backgroundConflict =
    !!payloadQuery.data?.existing_review && !payload?.existing_review && dirty
  React.useEffect(() => {
    const fresh = payloadQuery.data
    if (!fresh || fresh === displayedPayload) return
    if (
      displayedPayload?.task_id === fresh.task_id &&
      (dirty || (displayedPayload.existing_review && !fresh.existing_review))
    )
      return
    setDisplayedPayload(fresh)
  }, [payloadQuery.data, displayedPayload, dirty])

  React.useEffect(() => {
    if (search.task_id === undefined && !waitingForNext && payload) {
      void navigate({
        replace: true,
        search: { task_id: payload.task_id },
        ignoreBlocker: true,
      })
    }
  }, [navigate, payload, search.task_id, waitingForNext])
  React.useEffect(() => {
    if (payload) visit(payload.task_id)
  }, [payload, visit])
  React.useEffect(() => {
    if (!payload) return
    const key = `${payload.task_id}:${!!payload.existing_review}`
    if (initializedTask.current === key) return
    const changedTask =
      initializedTask.current.split(":")[0] !== String(payload.task_id)
    initializedTask.current = key
    if (changedTask) {
      setPending(null)
      setRecovery("idle")
      setErrorMessage(null)
      setCompletionMessage(null)
      setModelDirty(false)
    }
    if (payload.review_mode === "AUTHORED_RUBRIC") {
      const existing = payload.existing_review as {
        ratings: {
          fact_calls: string[]
          duplicate_flags: boolean[]
          multiple_facts_flags?: boolean[]
          looks_good: boolean[]
          [key: string]: unknown
        }
        comment?: string
        flags?: { confidence?: JudgmentConfidence }
      } | null
      const ratings = existing?.ratings
      const values = {
        factCalls: ratings?.fact_calls ?? initialFactCalls(payload.facts),
        duplicateFlags:
          ratings?.duplicate_flags ?? payload.facts.map(() => false),
        multipleFactsFlags:
          ratings?.multiple_facts_flags ?? payload.facts.map(() => false),
        looksGood: ratings?.looks_good ?? payload.facts.map(() => false),
        rubricValues: ratings
          ? Object.fromEntries(
              payload.rubric_dimensions.map((d) => [
                d.key,
                String(ratings[d.key]),
              ]),
            )
          : initialRubricValues(payload.rubric_dimensions),
        comments: existing?.comment ?? "",
        confidence: existing?.flags?.confidence ?? "EASY_CALL",
      }
      setFactCalls(values.factCalls)
      setDuplicateFlags(values.duplicateFlags)
      setMultipleFactsFlags(values.multipleFactsFlags)
      setLooksGood(values.looksGood)
      setRubricValues(values.rubricValues)
      setComments(values.comments)
      setConfidence(values.confidence)
      setBaseline(JSON.stringify(values))
    }
  }, [payload])

  const nextMutation = useMutation({
    mutationFn: async (isCurrent: () => boolean) => ({
      next: await readNextFactReview(),
      isCurrent,
    }),
    onSuccess: async ({ next, isCurrent }) => {
      if (!isCurrent()) return
      queryClient.setQueryData(nextKey, next)
      setErrorMessage(null)
      await navigate({
        search: { task_id: next?.task_id ?? undefined },
        ignoreBlocker: true,
      })
    },
    onError: (error, isCurrent) => {
      if (isCurrent()) setErrorMessage(apiErrorMessage(error))
    },
  })
  const invalidateSaved = () => {
    void queryClient.invalidateQueries({ queryKey: homeSummaryQueryKey })
    void queryClient.invalidateQueries({
      queryKey: nextKey,
      refetchType: "none",
    })
  }
  const reconcile = async (command: PendingReview, isCurrent = capture()) => {
    if (!isCurrent()) return
    setRecovery("saving")
    try {
      const fresh = await ReviewService.readFactDecompReview({
        taskId: command.taskId,
      })
      if (!isCurrent()) return
      if (fresh.existing_review) {
        const same = savedReviewMatches(fresh, command)
        await queryClient.cancelQueries({
          queryKey: ["review-fact-decomp", userId, command.taskId],
          exact: true,
        })
        if (!isCurrent()) return
        setDisplayedPayload(fresh)
        queryClient.setQueryData(
          ["review-fact-decomp", userId, command.taskId],
          fresh,
        )
        setModelDirty(false)
        setCompletionMessage(
          same
            ? "Saved review recovered."
            : "A different saved review already exists. Showing the saved version; download your local submission below.",
        )
        setRecovery(same ? "idle" : "different")
        if (same) setPending(null)
        setErrorMessage(null)
        invalidateSaved()
      } else {
        setRecovery("retry")
        setErrorMessage(
          "No saved review was found. Your edits are retained. Retry save when ready.",
        )
      }
    } catch {
      if (!isCurrent()) return
      setRecovery("uncertain")
      setErrorMessage(
        "Save outcome is unknown. Check save status or download your local copy before leaving.",
      )
    }
  }
  const submit = async (command: PendingReview) => {
    const isCurrent = capture()
    setPending(command)
    setRecovery("saving")
    setErrorMessage(null)
    try {
      if (command.mode === "MODEL_LABEL_CORRECTION") {
        await ReviewService.submitModelEvalReview({
          taskId: command.taskId,
          requestBody: command.body as ModelEvalReviewSubmit,
        })
      } else {
        await ReviewService.submitFactDecompReview({
          taskId: command.taskId,
          requestBody: command.body as FactDecompReviewSubmit,
        })
      }
      if (!isCurrent()) return
      setCompletionMessage("Review saved.")
      setModelDirty(false)
      setRecovery("idle")
      setPending(null)
      invalidateSaved()
      // Refresh saved history when revisited without delaying the next example.
      void queryClient.invalidateQueries({
        queryKey: ["review-fact-decomp", userId, command.taskId],
        refetchType: "none",
      })
      if (isCurrent()) nextMutation.mutate(isCurrent)
    } catch (error) {
      if (!isCurrent()) return
      if (isAuthoritativeClientError(error) && !hasApiErrorStatus(error, 409)) {
        setRecovery("idle")
        setPending(null)
        setErrorMessage(apiErrorMessage(error))
      } else await reconcile(command, isCurrent)
    }
  }
  const busy = uncertain || nextMutation.isPending
  const position = taskId === null ? visited.length : visited.indexOf(taskId)
  const previousId = visited[position - 1]
  const followingId = visited[position + 1]
  const navigation = (
    <>
      {dialog}
      {historyUnavailable ? (
        <p>
          Reload history is unavailable in this browser. Navigation still works
          in this tab.
        </p>
      ) : null}
      <nav aria-label="Example navigation" className="flex flex-wrap gap-2">
        <Button
          type="button"
          variant="outline"
          disabled={previousId === undefined}
          onClick={() => void navigate({ search: { task_id: previousId } })}
        >
          Previous example
        </Button>
        {(saved && taskId !== null) || (payloadQuery.isError && !payload) ? (
          <Button
            type="button"
            disabled={busy}
            onClick={() => {
              if (followingId !== undefined)
                void navigate({ search: { task_id: followingId } })
              else nextMutation.mutate(capture())
            }}
          >
            {nextMutation.isPending ? "Loading next example" : "Next example"}
          </Button>
        ) : null}
      </nav>
      {payloadQuery.isError && payload ? (
        <p>
          The latest review could not be checked. Your local edits are retained.
        </p>
      ) : null}
      {backgroundConflict ? (
        <div className="space-y-2" role="alert">
          <p>
            A review was saved in another tab. Your unfinished edits are
            retained. Download your local copy before loading the saved review.
          </p>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              onClick={() =>
                downloadLocalCopy(`review-${taskId}-unfinished.json`, {
                  taskId,
                  userId,
                  mode: payload?.review_mode,
                  local: JSON.parse(
                    payload?.review_mode === "MODEL_LABEL_CORRECTION"
                      ? modelSnapshot.current
                      : authoredValue,
                  ),
                })
              }
            >
              Download local copy
            </Button>
            <Button
              onClick={() =>
                confirmAction(() => {
                  const fresh = payloadQuery.data
                  if (fresh?.task_id === taskId) {
                    setDisplayedPayload(fresh)
                    setModelDirty(false)
                  }
                })
              }
            >
              Load saved review
            </Button>
          </div>
        </div>
      ) : null}
      {pending && recovery !== "saving" ? (
        <div className="flex flex-wrap gap-2">
          {recovery === "uncertain" ? (
            <Button onClick={() => void reconcile(pending)}>
              Check save status
            </Button>
          ) : null}

          <Button
            variant="outline"
            onClick={() =>
              downloadLocalCopy(`review-${pending.taskId}.json`, pending)
            }
          >
            Download local copy
          </Button>
        </div>
      ) : null}
    </>
  )
  const submitReview = () => {
    if (
      !payload ||
      payload.review_mode !== "AUTHORED_RUBRIC" ||
      saved ||
      backgroundConflict ||
      busy ||
      !isReviewActionAllowed(payload.allowed_actions, "save_review")
    )
      return
    void submit({
      taskId: payload.task_id,
      userId,
      mode: payload.review_mode,
      body: {
        fact_calls: factCalls,
        duplicate_flags: duplicateFlags,
        multiple_facts_flags: multipleFactsFlags,
        looks_good: looksGood,
        values: rubricValues,
        comments: comments.trim() || null,
        confidence,
        item_revision: payload.item_revision,
      },
    })
  }
  if (waitingForNext || payloadQuery.isLoading)
    return (
      <div>
        <p>Loading fact-decomposition review…</p>
        {dialog}
      </div>
    )
  if (search.task_id === undefined && nextQuery.isError)
    return (
      <div>
        <p>{apiErrorMessage(nextQuery.error)}</p>
        <Button onClick={() => void nextQuery.refetch()}>Retry queue</Button>
        {navigation}
      </div>
    )
  if (taskId === null)
    return (
      <div className="space-y-3">
        <h2 className="font-semibold">All caught up</h2>
        <p>
          No eligible fact-decomposition tasks remain across active datasets.
        </p>
        <Button
          disabled={nextQuery.isFetching}
          onClick={() => void nextQuery.refetch()}
        >
          Check for new examples
        </Button>
        {navigation}
      </div>
    )
  if (payloadQuery.isError && !payload)
    return (
      <div className="space-y-3">
        <p>
          This example is unavailable. {apiErrorMessage(payloadQuery.error)}
        </p>
        {navigation}
        <Button onClick={() => void navigate({ search: {} })}>
          Back to queue
        </Button>
      </div>
    )
  if (!payload) return null
  if (payload.review_mode === "MODEL_LABEL_CORRECTION")
    return (
      <div className="space-y-3">
        <p className="text-muted-foreground text-sm">
          {payload.dataset.display_name} · Task {payload.task_id}
        </p>
        <FactDecompositionCorrection
          saveLabel={recovery === "retry" ? "Retry save" : "Save and next"}
          key={`${payload.task_id}:${!!payload.existing_review}`}
          canSubmit={
            !backgroundConflict &&
            isReviewActionAllowed(payload.allowed_actions, "save_model_eval")
          }
          claims={correctionClaims(payload)}
          guide={payload.guide}
          existingReview={payload.existing_review}
          completionMessage={completionMessage}
          navigation={navigation}
          errorMessage={errorMessage}
          onDirtyChange={setModelDirty}
          onSnapshotChange={captureModelSnapshot}
          onSubmit={(submission) => {
            if (!busy && !backgroundConflict)
              void submit({
                taskId: payload.task_id,
                userId,
                mode: payload.review_mode,
                body: { ...submission, item_revision: payload.item_revision },
              })
          }}
          query={payload.user_prompt}
          response={payload.assistant_response}
          submitting={uncertain}
        />
      </div>
    )
  const authoredLocked =
    backgroundConflict ||
    saved ||
    uncertain ||
    !isReviewActionAllowed(payload.allowed_actions, "save_review")
  const authoredComplete = payload.facts.every(
    (fact, i) =>
      looksGood[i] ||
      duplicateFlags[i] ||
      multipleFactsFlags[i] ||
      factCalls[i] !== fact.polarity,
  )
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-muted-foreground text-sm">
            {payload.dataset.display_name}
          </p>
        </div>
        <Badge variant="outline">Task {payload.task_id}</Badge>
      </div>

      {navigation}
      <GradingInstructions guide={payload.guide} />
      <div className="grid gap-6 lg:grid-cols-[1fr_22rem]">
        <section className="space-y-5">
          <div className="space-y-3 rounded-md border p-4">
            <div className="text-sm font-medium">Statement</div>
            <p className="whitespace-pre-wrap text-base">
              {payload.item.prompt_text}
            </p>
          </div>

          <section className="space-y-3" data-testid="ordered-facts">
            <h2 className="text-base font-semibold tracking-normal">
              Ordered Facts
            </h2>
            {(payload.facts ?? []).map((fact, index) => (
              <div className="rounded-md border p-4" key={index}>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="text-xs text-muted-foreground">
                      Fact {fact.position + 1} ·{" "}
                      {looksGood[index] ||
                      duplicateFlags[index] ||
                      multipleFactsFlags[index] ||
                      factCalls[index] !== fact.polarity
                        ? "Reviewed"
                        : "Needs review"}
                      {duplicateFlags[index] ? " · Duplicate" : ""}
                      {multipleFactsFlags[index] ? " · Multiple Facts" : ""}
                    </div>
                    <p className="mt-1 whitespace-pre-wrap text-sm">
                      {fact.fact_text}
                    </p>
                  </div>
                  <Badge variant="secondary">
                    {optionLabel(fact.polarity)}
                  </Badge>
                </div>
                <div className="mt-3 space-y-2">
                  <Label>Reviewer call</Label>
                  <Select
                    disabled={authoredLocked}
                    onValueChange={(value) => {
                      setLooksGood((current) =>
                        current.map((v, i) => (i === index ? false : v)),
                      )
                      setFactCalls((current) =>
                        current.map((call, callIndex) =>
                          callIndex === index ? value : call,
                        ),
                      )
                    }}
                    value={factCalls[index] ?? fact.polarity}
                  >
                    <SelectTrigger
                      className="w-full"
                      data-testid={`fact-call-${index}`}
                    >
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {factCallOptions.map((entry) => (
                        <SelectItem key={entry.value} value={entry.value}>
                          {entry.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <fieldset
                    className="flex flex-wrap gap-2"
                    aria-label={`Fact ${fact.position + 1} review decision`}
                  >
                    <Button
                      type="button"
                      size="sm"
                      disabled={authoredLocked}
                      variant={looksGood[index] ? "default" : "outline"}
                      aria-pressed={!!looksGood[index]}
                      onClick={() => {
                        if (factCalls[index] === "MALFORMED") {
                          setFactCalls((current) =>
                            current.map((call, i) =>
                              i === index ? fact.polarity : call,
                            ),
                          )
                        }
                        setLooksGood((current) =>
                          current.map((v, i) => (i === index ? !v : v)),
                        )
                        setDuplicateFlags((current) =>
                          current.map((v, i) => (i === index ? false : v)),
                        )
                        setMultipleFactsFlags((current) =>
                          current.map((v, i) => (i === index ? false : v)),
                        )
                      }}
                    >
                      Looks good
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      disabled={authoredLocked}
                      variant={duplicateFlags[index] ? "default" : "outline"}
                      aria-pressed={!!duplicateFlags[index]}
                      onClick={() => {
                        setDuplicateFlags((current) =>
                          current.map((v, i) => (i === index ? !v : v)),
                        )
                        setLooksGood((current) =>
                          current.map((v, i) => (i === index ? false : v)),
                        )
                      }}
                    >
                      Duplicate
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      disabled={authoredLocked}
                      variant={
                        multipleFactsFlags[index] ? "default" : "outline"
                      }
                      aria-pressed={!!multipleFactsFlags[index]}
                      title="This claim contains multiple facts and is not atomic"
                      onClick={() => {
                        setMultipleFactsFlags((current) =>
                          current.map((v, i) => (i === index ? !v : v)),
                        )
                        setLooksGood((current) =>
                          current.map((v, i) => (i === index ? false : v)),
                        )
                      }}
                    >
                      Multiple Facts
                    </Button>
                  </fieldset>
                </div>
              </div>
            ))}
          </section>

          {(payload.chunks ?? []).length > 0 ? (
            <section className="space-y-3">
              <h2 className="text-base font-semibold tracking-normal">
                Source Chunks
              </h2>
              {(payload.chunks ?? []).map((chunk) => (
                <blockquote
                  className="whitespace-pre-wrap rounded-md border bg-muted/30 p-4 text-sm leading-7"
                  key={chunk.id}
                >
                  {chunk.text}
                </blockquote>
              ))}
            </section>
          ) : null}
        </section>

        <aside className="space-y-5">
          <fieldset disabled={authoredLocked} className="space-y-5">
            <section className="space-y-4">
              <h2 className="text-base font-semibold tracking-normal">
                Rubric
              </h2>
              {(payload.rubric_dimensions ?? []).map((dimension) => (
                <div className="space-y-2" key={dimension.key}>
                  <Label>{dimension.label}</Label>
                  <Select
                    disabled={authoredLocked}
                    onValueChange={(value) =>
                      setRubricValues((current) => ({
                        ...current,
                        [dimension.key]: value,
                      }))
                    }
                    value={
                      rubricValues[dimension.key] ??
                      dimension.options?.[0] ??
                      "pass"
                    }
                  >
                    <SelectTrigger
                      className="w-full"
                      data-testid={`rubric-${dimension.key}`}
                    >
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {(dimension.options ?? []).map((option) => (
                        <SelectItem key={option} value={option}>
                          {optionLabel(option)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              ))}
            </section>

            <div className="space-y-2">
              <Label>Confidence</Label>
              <Select
                disabled={authoredLocked}
                onValueChange={(value: JudgmentConfidence) =>
                  setConfidence(value)
                }
                value={confidence}
              >
                <SelectTrigger
                  className="w-full"
                  data-testid="confidence-select"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {confidences.map((entry) => (
                    <SelectItem key={entry.value} value={entry.value}>
                      {entry.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-2">
              <Label htmlFor="fact-review-comments">Comments</Label>
              <textarea
                className="border-input focus-visible:border-ring focus-visible:ring-ring/50 min-h-24 w-full rounded-md border bg-transparent px-3 py-2 text-sm shadow-xs outline-none focus-visible:ring-[3px]"
                id="fact-review-comments"
                onChange={(event) => setComments(event.target.value)}
                value={comments}
              />
            </div>

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

            <Button
              disabled={
                authoredLocked ||
                !authoredComplete ||
                uncertain ||
                completionMessage !== null ||
                !isReviewActionAllowed(payload.allowed_actions, "save_review")
              }
              onClick={submitReview}
              type="button"
            >
              {uncertain
                ? "Saving review"
                : recovery === "retry"
                  ? "Retry save"
                  : "Save and next"}
            </Button>
          </fieldset>
        </aside>
      </div>
    </div>
  )
}
