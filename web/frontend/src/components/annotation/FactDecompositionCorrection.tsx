import { Plus, Send } from "lucide-react"
import * as React from "react"

import type {
  ClaimReview,
  HumanClaim,
  ImportanceGuide,
  ModelCorrectionReview,
} from "@/client/types.gen"
import { Button } from "@/components/ui/button"
import { evalItemColor } from "@/lib/evalItemPalette"
import {
  ClaimCorrectionList,
  type ClaimGroup,
  type DraftHumanClaim,
  type ImportanceLabel,
  initialModelClaim,
  Labels,
} from "./ClaimCorrectionList"
import { GradingInstructions } from "./GradingInstructions"
import {
  type ClaimResponseSpan,
  normalizeResponseSpans,
  responseSelectionText,
  SelectableClaimResponse,
} from "./SelectableClaimResponse"

export type CorrectionInputClaim = {
  claim_text: string
  position: number
  response_spans: ClaimResponseSpan[]
  proposed_label: "vital" | "semi-important" | "unimportant"
}

export type CorrectionSubmission = ModelCorrectionReview

type FactDecompositionCorrectionProps = {
  query?: string | null
  response: string
  claims: CorrectionInputClaim[]
  guide: ImportanceGuide
  existingReview?: ModelCorrectionReview | null
  canSubmit: boolean
  submitting?: boolean
  errorMessage?: string | null
  completionMessage?: string | null
  onSubmit: (submission: CorrectionSubmission) => void
}

function completedHumanClaims(groups: ClaimGroup[]): HumanClaim[] | null {
  const humanGroups = groups.filter((group) => !group.original)
  if (humanGroups.some((group) => !group.claim.label)) return null
  return humanGroups.map((group) => ({
    ...group.claim,
    label: group.claim.label as ImportanceLabel,
  }))
}

export function FactDecompositionCorrection({
  canSubmit,
  existingReview,
  claims,
  guide,
  completionMessage = null,
  errorMessage = null,
  onSubmit,
  query,
  response,
  submitting = false,
}: FactDecompositionCorrectionProps) {
  const [groups, setGroups] = React.useState<ClaimGroup[]>(() => [
    ...claims.map((claim) => {
      const saved = existingReview?.claim_reviews.find(
        (review) => review.position === claim.position,
      )
      return {
        id: claim.position,
        original: claim,
        claim: {
          ...initialModelClaim(claim),
          label: saved ? (saved.label ?? undefined) : claim.proposed_label,
        },
        issue: saved?.issue,
        duplicate: saved?.duplicate ?? false,
        multipleFacts: saved?.multiple_facts ?? false,
        looksGood: saved?.looks_good ?? false,
      }
    }),
    ...(existingReview?.human_claims.map((claim, i) => ({
      id:
        Math.max(-1, ...claims.map((candidate) => candidate.position)) + 1 + i,
      claim,
    })) ?? []),
  ])
  const [hiddenModels, setHiddenModels] = React.useState(false)
  const [coverageChecked, setCoverageChecked] = React.useState(
    existingReview?.coverage_checked ?? false,
  )
  const [draft, setDraft] = React.useState<DraftHumanClaim | null>(null)
  const [stagedSpans, setStagedSpans] = React.useState<ClaimResponseSpan[]>([])
  const [activePosition, setActivePosition] = React.useState<number | null>(
    null,
  )
  const nextDraftId = React.useRef(
    Math.max(-1, ...groups.map((group) => group.id)) + 1,
  )
  const locked =
    !canSubmit || submitting || !!completionMessage || !!existingReview
  const owners = groups
    .filter((group) => !hiddenModels || !group.original)
    .map((group) => ({
      position: group.id,
      spans: group.claim.response_spans,
      dotClass: evalItemColor(group.id).dot,
      markClass: evalItemColor(group.id).mark,
    }))
  const humanClaims = completedHumanClaims(groups)
  const modelGroups = groups.filter((group) => group.original)
  const modelReviewsComplete = modelGroups.every(
    (group) =>
      !!group.looksGood ||
      !!group.duplicate ||
      !!group.multipleFacts ||
      (!!group.claim.label &&
        group.claim.label !== group.original?.proposed_label) ||
      (group.issue !== null &&
        group.issue !== undefined &&
        !!group.issue.trim()),
  )
  const issueNotesComplete = modelGroups.every(
    (group) =>
      group.issue === null || group.issue === undefined || !!group.issue.trim(),
  )

  const focusClaimRow = (position: number) => {
    setActivePosition(position)
    const row = document.getElementById(`claim-position-${position}`)
    if (row instanceof HTMLDetailsElement) row.open = true
    row?.scrollIntoView({ behavior: "smooth", block: "nearest" })
  }

  const focusSourceSpan = (position: number) => {
    setActivePosition(position)
    const sourceHighlight = [
      ...document.querySelectorAll<HTMLElement>("[data-owner-positions]"),
    ].find((element) =>
      element.dataset.ownerPositions?.split(",").includes(String(position)),
    )
    sourceHighlight?.scrollIntoView({ behavior: "smooth", block: "nearest" })
  }

  const createHumanClaim = () => {
    if (!stagedSpans.length || locked || draft) return
    setDraft({
      claim_text: responseSelectionText(response, stagedSpans),
      response_spans: [...stagedSpans],
    })
  }
  const saveHumanClaim = () => {
    if (!draft?.claim_text.trim() || !draft.label || locked) return
    const id = nextDraftId.current++
    setGroups((current) => [...current, { id, claim: draft }])
    setDraft(null)
  }

  const submit = () => {
    if (!humanClaims) return
    const claimReviews: ClaimReview[] = modelGroups.map((group) => ({
      position: group.original!.position,
      label: group.claim.label ?? null,
      issue: group.issue?.trim() || null,
      duplicate: group.duplicate ?? false,
      multiple_facts: group.multipleFacts ?? false,
      looks_good: group.looksGood ?? false,
    }))
    onSubmit({
      rubric_id: guide.rubric_id,
      claim_reviews: claimReviews,
      human_claims: humanClaims,
      coverage_checked: true,
    })
  }

  return (
    <div
      className="flex flex-col gap-6"
      data-testid="fact-decomposition-correction"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
            Fact decomposition
          </p>
          <h1 className="text-2xl font-semibold tracking-tight">
            Review extraction and importance
          </h1>
        </div>
        <div className="space-y-2">
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={hiddenModels}
              onChange={(event) => setHiddenModels(event.target.checked)}
            />
            Hide model results
          </label>
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(22rem,0.8fr)] lg:items-start">
        <section
          aria-label="Source and human claim editor"
          className="space-y-4 lg:sticky lg:top-0 lg:max-h-[calc(100dvh-8rem)] lg:overflow-y-auto lg:overscroll-contain"
        >
          {query?.trim() ? (
            <div className="rounded-xl border bg-card p-4 shadow-sm">
              <p className="mb-2 text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                Question
              </p>
              <p className="whitespace-pre-wrap text-sm leading-7">{query}</p>
            </div>
          ) : null}
          <div className="rounded-xl border bg-card p-4 shadow-sm">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                  Source text
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  Select exact text to add a missing claim.
                </p>
              </div>
              <span className="text-xs text-muted-foreground">
                {stagedSpans.length
                  ? `${stagedSpans.length} span${stagedSpans.length === 1 ? "" : "s"} staged`
                  : "No text staged"}
              </span>
            </div>
            <SelectableClaimResponse
              activePosition={activePosition}
              onHighlightClick={focusClaimRow}
              onSelectionChange={
                locked
                  ? undefined
                  : (spans, additive) =>
                      setStagedSpans((current) =>
                        normalizeResponseSpans(
                          response,
                          additive ? [...current, ...spans] : spans,
                        ),
                      )
              }
              owners={owners}
              response={response}
              stagedSpans={stagedSpans}
            />
            <div className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-dashed bg-muted/20 p-3">
              <span className="text-xs text-muted-foreground">
                Hold Cmd/Ctrl to select another span.
              </span>
              <Button
                disabled={locked || stagedSpans.length === 0 || draft !== null}
                onClick={createHumanClaim}
                size="sm"
                type="button"
                variant="outline"
              >
                <Plus /> Add missing claim
              </Button>
            </div>
          </div>
          {draft ? (
            <section
              className="rounded-xl border bg-card p-4 space-y-3"
              aria-label="New human claim"
            >
              <h2 className="text-xl font-semibold">New human claim</h2>
              <p className="border-l-2 border-primary pl-3 text-sm">
                {draft.response_spans.map((span) => span.text).join(" … ")}
              </p>
              <fieldset disabled={locked} className="space-y-3">
                <textarea
                  aria-label="New human claim text"
                  className="w-full rounded border bg-background p-2 text-sm"
                  maxLength={20000}
                  value={draft.claim_text}
                  onChange={(event) =>
                    setDraft({ ...draft, claim_text: event.target.value })
                  }
                />
                <Labels
                  name="New human claim"
                  value={draft.label}
                  labels={guide.labels}
                  onChange={(label) => setDraft({ ...draft, label })}
                />
                <div className="flex flex-wrap gap-2">
                  <Button
                    disabled={!draft.claim_text.trim() || !draft.label}
                    onClick={saveHumanClaim}
                  >
                    Add human claim
                  </Button>
                  <Button variant="ghost" onClick={() => setDraft(null)}>
                    Cancel
                  </Button>
                </div>
              </fieldset>
            </section>
          ) : null}
        </section>

        <div className="space-y-4">
          <GradingInstructions guide={guide} />
          <ClaimCorrectionList
            activePosition={activePosition}
            groups={groups}
            guide={guide}
            locked={locked}
            stagedSpans={stagedSpans}
            onFocusClaim={focusSourceSpan}
            hiddenModels={hiddenModels}
            onChange={(id, claim, issue, duplicate, looksGood, multipleFacts) =>
              setGroups((current) =>
                current.map((group) =>
                  group.id === id
                    ? {
                        ...group,
                        claim,
                        issue,
                        duplicate,
                        looksGood,
                        multipleFacts,
                      }
                    : group,
                ),
              )
            }
            onRemove={(id) =>
              setGroups((current) =>
                current.filter((group) => group.original || group.id !== id),
              )
            }
          />
          <label className="flex items-start gap-2 rounded-xl border p-4 text-sm">
            <input
              type="checkbox"
              checked={coverageChecked}
              disabled={locked}
              onChange={(event) => setCoverageChecked(event.target.checked)}
            />
            I checked the text for missing worthwhile claims
          </label>
          <p className="text-sm text-muted-foreground">
            {claims.length} model claims · {humanClaims?.length ?? 0} added
            claims
          </p>
          {draft ? (
            <p className="text-sm">
              Add or cancel the draft before saving your review.
            </p>
          ) : null}
          {existingReview ? (
            <p className="text-sm">Previously submitted review. Read only.</p>
          ) : null}
          {errorMessage ? (
            <div
              className="rounded-lg border border-destructive/40 p-3 text-sm"
              role="alert"
            >
              {errorMessage}
            </div>
          ) : null}
          {completionMessage ? (
            <div className="rounded-lg border border-emerald-500/40 bg-emerald-500/5 p-3 text-sm">
              {completionMessage}
            </div>
          ) : null}
          <Button
            className="w-full"
            disabled={
              locked ||
              draft !== null ||
              humanClaims === null ||
              humanClaims.length > 10000 ||
              humanClaims.some((claim) => !claim.claim_text.trim()) ||
              !modelReviewsComplete ||
              !issueNotesComplete ||
              !coverageChecked
            }
            onClick={submit}
            type="button"
          >
            <Send /> {submitting ? "Saving review" : "Save review"}
          </Button>
        </div>
      </div>
    </div>
  )
}
