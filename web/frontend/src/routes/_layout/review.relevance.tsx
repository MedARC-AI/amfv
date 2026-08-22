import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { Loader2 } from "lucide-react"
import * as React from "react"

import {
  type JudgmentConfidence,
  type RelevanceReviewSubmit,
  ReviewService,
} from "@/client"
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
import { apiErrorMessage, hasApiErrorStatus } from "@/utils"

export const Route = createFileRoute("/_layout/review/relevance")({
  component: RelevanceReview,
  head: () => ({
    meta: [
      {
        title: "Relevance Review - AMFV Web",
      },
    ],
  }),
})

const grades = [
  { value: 3, label: "Highly relevant" },
  { value: 2, label: "Partially relevant" },
  { value: 1, label: "Weakly relevant" },
  { value: 0, label: "Not relevant" },
]

const confidences: Array<{ value: JudgmentConfidence; label: string }> = [
  { value: "EASY_CALL", label: "Easy call" },
  { value: "DELIBERATED", label: "Deliberated" },
]

function RelevanceReview() {
  const queryClient = useQueryClient()
  const [grade, setGrade] = React.useState(3)
  const [confidence, setConfidence] =
    React.useState<JudgmentConfidence>("EASY_CALL")
  const [completionMessage, setCompletionMessage] = React.useState<
    string | null
  >(null)
  const [errorMessage, setErrorMessage] = React.useState<string | null>(null)

  const nextQuery = useQuery({
    queryKey: ["review-next", "relevance"],
    queryFn: () =>
      ReviewService.readNextReviewTask({
        evalType: "RETRIEVAL",
        mode: "RELEVANCE",
      }),
    retry: false,
  })

  const claimMutation = useMutation({
    mutationFn: () =>
      ReviewService.claimNextReviewTask({
        requestBody: {
          eval_type: "RETRIEVAL",
          mode: "RELEVANCE",
        },
      }),
    onSuccess: (recommendation) => {
      setErrorMessage(null)
      queryClient.setQueryData(["review-next", "relevance"], recommendation)
    },
    onError: (error) => setErrorMessage(apiErrorMessage(error)),
  })

  const assignmentId = nextQuery.data?.assignment_id ?? null

  const payloadQuery = useQuery({
    queryKey: ["review-relevance", assignmentId],
    queryFn: () =>
      ReviewService.readRelevanceReview({
        assignmentId: assignmentId as number,
      }),
    enabled: assignmentId !== null,
    retry: false,
  })

  const payload = payloadQuery.data

  const submitMutation = useMutation({
    mutationFn: (requestBody: RelevanceReviewSubmit) =>
      ReviewService.submitRelevanceReview({
        assignmentId: payload?.assignment_id as number,
        requestBody,
      }),
    onSuccess: (response) => {
      setCompletionMessage(
        `Relevance review submitted for item ${response.item_id}.`,
      )
      setErrorMessage(null)
    },
    onError: (error) => setErrorMessage(apiErrorMessage(error)),
  })

  const submitReview = () => {
    if (
      !payload ||
      completionMessage ||
      !isReviewActionAllowed(payload.allowed_actions, "grade_relevance")
    ) {
      return
    }
    submitMutation.mutate({
      grade,
      confidence,
      item_revision: payload.item_revision,
    })
  }

  if (nextQuery.isLoading || payloadQuery.isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />
        Loading relevance review
      </div>
    )
  }

  if (nextQuery.isError && !hasApiErrorStatus(nextQuery.error, 404)) {
    return (
      <div className="flex flex-col gap-3">
        <div className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
          {apiErrorMessage(nextQuery.error)}
        </div>
      </div>
    )
  }

  if (assignmentId === null) {
    return (
      <div className="flex flex-col gap-3">
        <div className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
          No relevance review assignment is currently held by you. Claim the
          next available task to begin reviewing.
        </div>
        {errorMessage ? (
          <div
            className="rounded-md border border-destructive/40 p-3 text-sm"
            role="alert"
          >
            {errorMessage}
          </div>
        ) : null}
        <Button
          disabled={claimMutation.isPending}
          onClick={() => {
            setErrorMessage(null)
            claimMutation.mutate()
          }}
          type="button"
        >
          {claimMutation.isPending
            ? "Claiming relevance review"
            : "Claim relevance review"}
        </Button>
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

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-muted-foreground text-sm">
            {payload.dataset.display_name}
          </p>
        </div>
        <Badge variant="outline">
          {nextQuery.data?.reservation_state === "existing"
            ? "Existing assignment"
            : "New assignment"}
        </Badge>
      </div>

      <div className="grid gap-6 lg:grid-cols-[1fr_22rem]">
        <section className="space-y-5">
          <div className="space-y-3 rounded-md border p-4">
            <div className="text-sm font-medium">Item Prompt</div>
            <p className="whitespace-pre-wrap text-base">
              {payload.item.prompt_text}
            </p>
            {payload.item.expected_answer ? (
              <div className="rounded-md bg-muted/40 p-3 text-sm">
                <span className="font-medium">Expected answer: </span>
                {payload.item.expected_answer}
              </div>
            ) : null}
          </div>

          <div className="space-y-3 rounded-md border p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <h2 className="text-base font-semibold tracking-normal">
                  Candidate Passage
                </h2>
                <p className="text-muted-foreground text-sm">
                  {payload.document.title}
                </p>
              </div>
              <Badge variant="secondary">
                Candidate {payload.candidate.id ?? "unpersisted"}
              </Badge>
            </div>
            <blockquote className="whitespace-pre-wrap rounded-md bg-muted/30 p-4 text-sm leading-7">
              {payload.chunk.text}
            </blockquote>
          </div>
        </section>

        <aside className="space-y-5">
          <div className="space-y-2">
            <Label>Relevance grade</Label>
            <Select
              onValueChange={(value) => setGrade(Number(value))}
              value={grade.toString()}
            >
              <SelectTrigger className="w-full" data-testid="grade-select">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {grades.map((entry) => (
                  <SelectItem key={entry.value} value={entry.value.toString()}>
                    {entry.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

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

          <div className="rounded-md border p-3 text-sm">
            <div className="font-medium">Candidate metadata</div>
            <dl className="mt-2 space-y-1 text-muted-foreground">
              <div className="flex justify-between gap-3">
                <dt>System count</dt>
                <dd>{payload.candidate.systems?.length ?? 0}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt>Calibration</dt>
                <dd>{payload.candidate.is_calibration ? "Yes" : "No"}</dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt>Reference grade</dt>
                <dd>{payload.candidate.reference_grade ?? "None"}</dd>
              </div>
            </dl>
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
              !isReviewActionAllowed(payload.allowed_actions, "grade_relevance")
            }
            onClick={submitReview}
            type="button"
          >
            {submitMutation.isPending ? "Submitting" : "Submit relevance"}
          </Button>
        </aside>
      </div>
    </div>
  )
}
