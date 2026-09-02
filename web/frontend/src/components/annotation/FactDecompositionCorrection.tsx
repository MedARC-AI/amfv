import { Plus, Send } from "lucide-react"
import * as React from "react"

import { Button } from "@/components/ui/button"
import { evalItemColor } from "@/lib/evalItemPalette"
import {
  ClaimCorrectionList,
  type ClaimLabel,
  type CorrectionClaim,
  type MissingClaimDraft,
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
  final_labels: ClaimLabel[]
  missing_claims: Array<{
    claim_text: string
    response_spans: ClaimResponseSpan[]
    label: ClaimLabel
  }>
}

type FactDecompositionCorrectionProps = {
  query?: string | null
  response: string
  claims: CorrectionInputClaim[]
  canSubmit: boolean
  submitting?: boolean
  errorMessage?: string | null
  completionMessage?: string | null
  onSubmit: (submission: CorrectionSubmission) => void
}

export function FactDecompositionCorrection({
  canSubmit,
  claims,
  completionMessage = null,
  errorMessage = null,
  onSubmit,
  query,
  response,
  submitting = false,
}: FactDecompositionCorrectionProps) {
  const [labels, setLabels] = React.useState<Record<number, ClaimLabel>>(() =>
    Object.fromEntries(
      claims.map((claim) => [claim.position, claim.proposed_label]),
    ),
  )
  const [missingClaims, setMissingClaims] = React.useState<MissingClaimDraft[]>(
    [],
  )
  const [stagedSpans, setStagedSpans] = React.useState<ClaimResponseSpan[]>([])
  const [activePosition, setActivePosition] = React.useState<number | null>(
    null,
  )
  const nextDraftId = React.useRef(0)
  const correctionClaims: CorrectionClaim[] = claims.map((claim) => ({
    claim: claim.claim_text,
    colorClass: evalItemColor(claim.position).card,
    dotClass: evalItemColor(claim.position).dot,
    position: claim.position,
    proposedLabel: claim.proposed_label,
    spans: claim.response_spans,
  }))
  const owners = [
    ...correctionClaims.map((claim) => ({
      dotClass: evalItemColor(claim.position).dot,
      markClass: evalItemColor(claim.position).mark,
      position: claim.position,
      spans: claim.spans,
    })),
    ...missingClaims.map((claim) => ({
      dotClass: evalItemColor(claim.position).dot,
      markClass: evalItemColor(claim.position).mark,
      position: claim.position,
      spans: claim.spans,
    })),
  ]

  const focusClaimRow = (position: number) => {
    setActivePosition(position)
    document
      .getElementById(`claim-position-${position}`)
      ?.scrollIntoView({ behavior: "smooth", block: "nearest" })
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

  const addMissingClaim = () => {
    if (stagedSpans.length === 0) return
    const claimText = responseSelectionText(response, stagedSpans)
    if (!claimText.trim()) return
    // The draft counter is monotonic: removing a draft never changes the
    // palette slot or position of another staged claim.
    const position = claims.length + nextDraftId.current
    const draftId = `missing-${nextDraftId.current++}`
    const color = evalItemColor(position)
    setMissingClaims((current) => [
      ...current,
      {
        claim: claimText,
        colorClass: color.card,
        draftId,
        dotClass: color.dot,
        label: "vital",
        position,
        spans: stagedSpans,
      },
    ])
    setStagedSpans([])
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
            Correct the model labels
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            The large proposal badges show the model’s starting labels. Select a
            different label when your judgment differs.
          </p>
        </div>
        <span className="rounded-full border px-3 py-1 text-xs font-medium text-muted-foreground">
          Response review
        </span>
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(22rem,0.8fr)] lg:items-start">
        <section className="space-y-4">
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
                  Assistant response
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  Click a color to focus its claim. Select exact text to add an
                  omitted claim.
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
              onSelectionChange={(spans, additive) =>
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
                disabled={stagedSpans.length === 0}
                onClick={addMissingClaim}
                size="sm"
                type="button"
                variant="outline"
              >
                <Plus /> Add missing claim
              </Button>
            </div>
          </div>
        </section>

        <div className="space-y-4">
          <ClaimCorrectionList
            activePosition={activePosition}
            claims={correctionClaims}
            labels={labels}
            missingClaims={missingClaims}
            onFocusClaim={focusSourceSpan}
            onLabelChange={(position, label) =>
              setLabels((current) => ({ ...current, [position]: label }))
            }
            onMissingChange={(draftId, patch) =>
              setMissingClaims((current) =>
                current.map((claim) =>
                  claim.draftId === draftId ? { ...claim, ...patch } : claim,
                ),
              )
            }
            onRemoveMissing={(draftId) =>
              setMissingClaims((current) =>
                current.filter((claim) => claim.draftId !== draftId),
              )
            }
          />
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
            disabled={!canSubmit || submitting || completionMessage !== null}
            onClick={() =>
              onSubmit({
                final_labels: claims.map(
                  (claim) => labels[claim.position] ?? claim.proposed_label,
                ),
                missing_claims: missingClaims.map(
                  ({ claim, label, spans }) => ({
                    claim_text: claim,
                    label,
                    response_spans: spans,
                  }),
                ),
              })
            }
            type="button"
          >
            <Send />{" "}
            {submitting ? "Submitting correction" : "Submit correction"}
          </Button>
        </div>
      </div>
    </div>
  )
}
