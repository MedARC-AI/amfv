import { useMutation, useQuery } from "@tanstack/react-query"
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
import { isReviewActionAllowed } from "@/reviewCapabilities"
import { apiErrorMessage } from "@/utils"

export const Route = createFileRoute("/_layout/review/fact-decomposition")({
  component: FactDecompositionReview,
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

function FactDecompositionReview() {
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
    queryFn: () =>
      ReviewService.readNextReviewTask({
        evalType: "FACT_DECOMP",
        mode: "ITEM_AUDIT",
      }),
    retry: false,
  })

  const taskId = nextQuery.data?.task_id ?? null

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
    if (!payload || initializedTaskId.current === payload.task_id) {
      return
    }
    initializedTaskId.current = payload.task_id
    if (payload.review_mode === "AUTHORED_RUBRIC") {
      setFactCalls(initialFactCalls(payload.facts))
      setRubricValues(initialRubricValues(payload.rubric_dimensions))
    }
    setComments("")
    setCompletionMessage(null)
    setErrorMessage(null)
    setModelSubmitting(false)
  }, [payload])

  const submitMutation = useMutation({
    mutationFn: (requestBody: FactDecompReviewSubmit) =>
      ReviewService.submitFactDecompReview({
        taskId: payload?.task_id as number,
        requestBody,
      }),
    onSuccess: (response) => {
      setCompletionMessage(
        `Fact review submitted for item ${response.item_id}.`,
      )
      setErrorMessage(null)
    },
    onError: (error) => setErrorMessage(apiErrorMessage(error)),
  })

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
      values: rubricValues,
      comments: comments.trim() || null,
      confidence,
      item_revision: payload.item_revision,
    })
  }

  if (nextQuery.isLoading || payloadQuery.isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />
        Loading fact-decomposition review
      </div>
    )
  }

  if (nextQuery.isError) {
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
          No fact-decomposition review tasks are available.
        </div>
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
        completionMessage={completionMessage}
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
            .then(() => {
              setCompletionMessage("Correction submitted.")
            })
            .catch((error: unknown) => setErrorMessage(apiErrorMessage(error)))
            .finally(() => setModelSubmitting(false))
        }}
        query={payload.user_prompt}
        response={payload.assistant_response}
        submitting={modelSubmitting}
      />
    )
  }

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
                      Fact {fact.position + 1}
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
                    onValueChange={(value) =>
                      setFactCalls((current) =>
                        current.map((call, callIndex) =>
                          callIndex === index ? value : call,
                        ),
                      )
                    }
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
          <section className="space-y-4">
            <h2 className="text-base font-semibold tracking-normal">Rubric</h2>
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
              <SelectTrigger className="w-full" data-testid="confidence-select">
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
              submitMutation.isPending ||
              completionMessage !== null ||
              !isReviewActionAllowed(payload.allowed_actions, "save_review")
            }
            onClick={submitReview}
            type="button"
          >
            {submitMutation.isPending ? "Submitting" : "Submit fact review"}
          </Button>
        </aside>
      </div>
    </div>
  )
}
