import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { Loader2 } from "lucide-react"
import * as React from "react"

import {
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
import useAuth from "@/hooks/useAuth"
import { homeSummaryQueryKey } from "@/lib/queries"
import { isReviewActionAllowed } from "@/reviewCapabilities"
import { apiErrorMessage, hasApiErrorStatus } from "@/utils"

export const Route = createFileRoute("/_layout/review/fact-decomposition")({
  component: FactDecompositionReview,
  validateSearch: (
    search: Record<string, unknown>,
  ): { task_id?: number; complete?: boolean } => {
    const value = Number(search.task_id)
    return {
      task_id: Number.isInteger(value) && value > 0 ? value : undefined,
      complete:
        search.complete === true || search.complete === "true"
          ? true
          : undefined,
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
  const queryClient = useQueryClient()
  const { user } = useAuth()
  const [visited, setVisited] = React.useState<number[]>(
    () =>
      queryClient.getQueryData<number[]>(["fact-review-history", user?.id]) ??
      [],
  )
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
  const [modelSubmitting, setModelSubmitting] = React.useState(false)
  const initializedTaskId = React.useRef<number | null>(null)

  const nextQuery = useQuery({
    queryKey: ["review-next", "fact-decomp"],
    queryFn: readNextFactReview,
    retry: false,
    enabled: search.task_id === undefined && !search.complete,
    refetchOnMount: "always",
    refetchOnWindowFocus: false,
  })

  // Wait for a fresh queue response before pinning a recommendation in the URL.
  const waitingForNext =
    search.task_id === undefined &&
    !search.complete &&
    (!nextQuery.isFetchedAfterMount || nextQuery.isFetching)
  const taskId =
    search.task_id ??
    (search.complete || waitingForNext || nextQuery.isError
      ? null
      : nextQuery.data?.task_id) ??
    null

  const payloadQuery = useQuery({
    queryKey: ["review-fact-decomp", taskId],
    queryFn: () =>
      ReviewService.readFactDecompReview({
        taskId: taskId as number,
      }),
    enabled: taskId !== null,
    retry: false,
  })

  const payload = payloadQuery.data

  React.useEffect(() => {
    if (
      search.task_id === undefined &&
      !waitingForNext &&
      !search.complete &&
      payload
    ) {
      void navigate({ replace: true, search: { task_id: payload.task_id } })
    }
  }, [navigate, payload, search.task_id, search.complete, waitingForNext])

  React.useEffect(() => {
    if (!payload) return
    setVisited((current) =>
      current.includes(payload.task_id)
        ? current
        : [...current, payload.task_id],
    )
  }, [payload])

  React.useEffect(() => {
    queryClient.setQueryData(["fact-review-history", user?.id], visited)
  }, [queryClient, visited, user?.id])

  React.useEffect(() => {
    if (!payload || initializedTaskId.current === payload.task_id) {
      return
    }
    initializedTaskId.current = payload.task_id
    if (payload.review_mode === "AUTHORED_RUBRIC") {
      const existing = payload.existing_review as {
        ratings: {
          fact_calls: string[]
          multiple_facts_flags?: boolean[]
          duplicate_flags: boolean[]
          looks_good: boolean[]
          [key: string]: unknown
        }
        comment?: string
        flags?: { confidence?: JudgmentConfidence }
      } | null
      const saved = existing?.ratings
      setFactCalls(saved?.fact_calls ?? initialFactCalls(payload.facts))
      setDuplicateFlags(
        saved?.duplicate_flags ?? payload.facts.map(() => false),
      )
      setMultipleFactsFlags(
        saved?.multiple_facts_flags ?? payload.facts.map(() => false),
      )
      setLooksGood(saved?.looks_good ?? payload.facts.map(() => false))
      setRubricValues(
        saved
          ? Object.fromEntries(
              payload.rubric_dimensions.map((d) => [
                d.key,
                String(saved[d.key]),
              ]),
            )
          : initialRubricValues(payload.rubric_dimensions),
      )
      setComments(existing?.comment ?? "")
      setConfidence(existing?.flags?.confidence ?? "EASY_CALL")
    }
    if (payload.review_mode !== "AUTHORED_RUBRIC") setComments("")
    setCompletionMessage(null)
    setErrorMessage(null)
    setModelSubmitting(false)
  }, [payload])

  const nextMutation = useMutation({
    mutationFn: readNextFactReview,
    onSuccess: async (next) => {
      queryClient.setQueryData(["review-next", "fact-decomp"], next)
      setErrorMessage(null)
      await navigate({
        search: {
          task_id: next?.task_id ?? undefined,
          complete: next?.task_id == null ? true : undefined,
        },
      })
    },
    onError: (error) => setErrorMessage(apiErrorMessage(error)),
  })

  const advanceAfterSave = async () => {
    setCompletionMessage("Review saved.")
    setErrorMessage(null)
    void queryClient.invalidateQueries({ queryKey: homeSummaryQueryKey })
    await queryClient.invalidateQueries({
      queryKey: ["review-fact-decomp", taskId],
    })
    await queryClient.invalidateQueries({
      queryKey: ["review-next", "fact-decomp"],
      refetchType: "none",
    })
    nextMutation.mutate()
  }

  const submitMutation = useMutation({
    mutationFn: (requestBody: FactDecompReviewSubmit) =>
      ReviewService.submitFactDecompReview({
        taskId: payload?.task_id as number,
        requestBody,
      }),
    onSuccess: advanceAfterSave,
    onError: (error) => setErrorMessage(apiErrorMessage(error)),
  })

  const saved = !!completionMessage || !!payload?.existing_review
  const busy =
    modelSubmitting || submitMutation.isPending || nextMutation.isPending
  const position = taskId === null ? visited.length : visited.indexOf(taskId)
  const previousId = visited[position - 1]
  const followingId = visited[position + 1]
  const navigation = (
    <nav aria-label="Example navigation" className="flex flex-wrap gap-2">
      <Button
        type="button"
        variant="outline"
        disabled={previousId === undefined || busy}
        onClick={() => {
          if (
            payload &&
            !saved &&
            !window.confirm("Leave this example? Unsaved changes will be lost.")
          )
            return
          void navigate({ search: { task_id: previousId } })
        }}
      >
        Previous example
      </Button>
      {saved && taskId !== null ? (
        <Button
          type="button"
          disabled={busy}
          onClick={() => {
            if (followingId !== undefined) {
              void navigate({ search: { task_id: followingId } })
            } else {
              nextMutation.mutate()
            }
          }}
        >
          {nextMutation.isPending ? "Loading next example" : "Next example"}
        </Button>
      ) : null}
    </nav>
  )

  const submitReview = () => {
    if (
      !payload ||
      payload.review_mode !== "AUTHORED_RUBRIC" ||
      completionMessage ||
      !isReviewActionAllowed(payload.allowed_actions, "save_review")
    ) {
      return
    }
    submitMutation.mutate({
      fact_calls: factCalls,
      duplicate_flags: duplicateFlags,
      multiple_facts_flags: multipleFactsFlags,
      looks_good: looksGood,
      values: rubricValues,
      comments: comments.trim() || null,
      confidence,
      item_revision: payload.item_revision,
    })
  }

  if (waitingForNext || payloadQuery.isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />
        Loading fact-decomposition review
      </div>
    )
  }

  if (search.task_id === undefined && !search.complete && nextQuery.isError) {
    return (
      <div className="flex flex-col gap-3">
        <div className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
          {apiErrorMessage(nextQuery.error)}
        </div>
      </div>
    )
  }

  if (taskId === null) {
    return (
      <div className="flex flex-col gap-3">
        <div className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
          <h2 className="font-semibold">All caught up</h2>
          <p>No fact-decomposition review tasks are available.</p>
        </div>
        {navigation}
      </div>
    )
  }

  if (payloadQuery.isError) {
    return (
      <div className="flex flex-col gap-3">
        <div className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
          {apiErrorMessage(payloadQuery.error)}
        </div>
      </div>
    )
  }

  if (!payload) {
    return null
  }

  if (payload.review_mode === "MODEL_LABEL_CORRECTION") {
    return (
      <FactDecompositionCorrection
        key={payload.task_id}
        canSubmit={isReviewActionAllowed(
          payload.allowed_actions,
          "save_model_eval",
        )}
        claims={correctionClaims(payload)}
        guide={payload.guide}
        existingReview={payload.existing_review}
        completionMessage={completionMessage}
        navigation={navigation}
        errorMessage={errorMessage}
        onSubmit={(submission) => {
          setErrorMessage(null)
          setModelSubmitting(true)
          const requestBody: ModelEvalReviewSubmit = {
            ...submission,
            item_revision: payload.item_revision,
          }
          ReviewService.submitModelEvalReview({
            taskId: taskId as number,
            requestBody,
          })
            .then(advanceAfterSave)
            .catch((error: unknown) => setErrorMessage(apiErrorMessage(error)))
            .finally(() => setModelSubmitting(false))
        }}
        query={payload.user_prompt}
        response={payload.assistant_response}
        submitting={modelSubmitting}
      />
    )
  }

  const authoredLocked =
    !!payload.existing_review ||
    submitMutation.isPending ||
    !!completionMessage ||
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
                submitMutation.isPending ||
                completionMessage !== null ||
                !isReviewActionAllowed(payload.allowed_actions, "save_review")
              }
              onClick={submitReview}
              type="button"
            >
              {submitMutation.isPending ? "Saving review" : "Save and next"}
            </Button>
          </fieldset>
        </aside>
      </div>
    </div>
  )
}
