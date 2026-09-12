import { Plus, Send } from "lucide-react"
import * as React from "react"

import type { HumanClaim } from "@/client/types.gen"
import { Button } from "@/components/ui/button"
import { evalItemColor } from "@/lib/evalItemPalette"
import {
  ClaimCorrectionList,
  type ClaimGroup,
  type ClaimLabel,
  initialModelClaim,
  Labels,
} from "./ClaimCorrectionList"
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
  proposed_label: ClaimLabel
}

export type CorrectionSubmission = {
  model_labels: ClaimLabel[]
  human_claims: HumanClaim[]
}

type FactDecompositionCorrectionProps = {
  query?: string | null
  response: string
  claims: CorrectionInputClaim[]
  existingReview?: CorrectionSubmission | null
  canSubmit: boolean
  submitting?: boolean
  errorMessage?: string | null
  completionMessage?: string | null
  onSubmit: (submission: CorrectionSubmission) => void
}

export function FactDecompositionCorrection({
  canSubmit,
  existingReview,
  claims,
  completionMessage = null,
  errorMessage = null,
  onSubmit,
  query,
  response,
  submitting = false,
}: FactDecompositionCorrectionProps) {
  const [groups, setGroups] = React.useState<ClaimGroup[]>(() => [
    ...claims.map((claim, index) => ({
      id: claim.position,
      original: claim,
      claim: {
        ...initialModelClaim(claim),
        label: existingReview?.model_labels[index] ?? claim.proposed_label,
      },
    })),
    ...(existingReview?.human_claims.map((claim, i) => ({
      id: Math.max(-1, ...claims.map((c) => c.position)) + 1 + i,
      claim,
    })) ?? []),
  ])
  const [hiddenModels, setHiddenModels] = React.useState(false)
  const [draft, setDraft] = React.useState<HumanClaim | null>(null)
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
  const humanClaims = groups
    .filter((group) => !group.original)
    .map((group) => group.claim)

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
      label: "substantive",
      response_spans: [...stagedSpans],
    })
  }
  const saveHumanClaim = () => {
    if (!draft?.claim_text.trim() || locked) return
    const id = nextDraftId.current++
    setGroups((current) => [...current, { id, claim: draft }])
    setDraft(null)
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
            Review verification relevance
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            Review every claim, including incidental claims. Substantive and
            borderline claims are included in verification. Labels describe
            relevance, not truth. Grade model claims and add your own claims
            where needed.
          </p>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={hiddenModels}
            onChange={(event) => setHiddenModels(event.target.checked)}
          />
          Hide model results
        </label>
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(22rem,0.8fr)] lg:items-start">
        <section
          aria-label="Source and human claim editor"
          className="space-y-4 lg:sticky lg:top-0 lg:max-h-[calc(100dvh-8rem)] lg:overflow-y-auto lg:overscroll-contain"
        >
          {query?.trim() ? (
            <div className="rounded-xl border bg-card p-4 shadow-sm">
              <p className="mb-2 text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                User prompt
              </p>
              <p className="whitespace-pre-wrap text-sm leading-7">{query}</p>
            </div>
          ) : null}
          <div className="rounded-xl border bg-card p-4 shadow-sm">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">
                  Response or document
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  Click a color to focus its claim. Select exact text to add a
                  human claim.
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
                Hold Cmd/Ctrl while selecting to add another discontiguous span.
              </span>
              <Button
                disabled={locked || stagedSpans.length === 0 || draft !== null}
                onClick={createHumanClaim}
                size="sm"
                type="button"
                variant="outline"
              >
                <Plus /> Create human claim
              </Button>
            </div>
          </div>
          {draft && (
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
                  onChange={(label) => setDraft({ ...draft, label })}
                />
                <div className="flex flex-wrap gap-2">
                  <Button
                    disabled={!draft.claim_text.trim()}
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
          )}
        </section>

        <div className="space-y-4">
          <ClaimCorrectionList
            activePosition={activePosition}
            groups={groups}
            locked={locked}
            stagedSpans={stagedSpans}
            onFocusClaim={focusSourceSpan}
            hiddenModels={hiddenModels}
            onChange={(id, claim) =>
              setGroups((current) =>
                current.map((group) =>
                  group.id === id ? { ...group, claim } : group,
                ),
              )
            }
            onRemove={(id) =>
              setGroups((current) =>
                current.filter((group) => group.original || group.id !== id),
              )
            }
          />
          <p className="text-sm text-muted-foreground">
            {claims.length} model grades · {humanClaims.length} additional human
            claims
          </p>
          {draft && (
            <p className="text-sm">
              Add or cancel the draft before saving your review.
            </p>
          )}
          {existingReview && (
            <p className="text-sm">Previously submitted review. Read only.</p>
          )}
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
              humanClaims.length > 10000 ||
              humanClaims.some((claim) => !claim.claim_text.trim())
            }
            onClick={() =>
              onSubmit({
                model_labels: groups
                  .filter((group) => group.original)
                  .map((group) => group.claim.label),
                human_claims: humanClaims,
              })
            }
            type="button"
          >
            <Send /> {submitting ? "Saving review" : "Save review"}
          </Button>
        </div>
      </div>
    </div>
  )
}
