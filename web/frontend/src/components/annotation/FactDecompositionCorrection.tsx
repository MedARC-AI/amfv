import { Plus, Send } from "lucide-react"
import * as React from "react"

import type { FinalModelClaim } from "@/client/types.gen"
import { Button } from "@/components/ui/button"
import { evalItemColor } from "@/lib/evalItemPalette"
import {
  ClaimCorrectionList,
  type ClaimGroup,
  type ClaimLabel,
  originalFinal,
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

export type CorrectionSubmission = { final_claims: FinalModelClaim[] }

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
    ...claims.map((claim) => ({
      id: claim.position,
      original: claim,
      finals: existingReview
        ? existingReview.final_claims.filter(
            (final) => final.original_position === claim.position,
          )
        : [originalFinal(claim)],
    })),
    ...(existingReview?.final_claims
      .filter((claim) => claim.original_position === null)
      .map((claim, i) => ({
        id: Math.max(-1, ...claims.map((c) => c.position)) + 1 + i,
        finals: [claim],
      })) ?? []),
  ])
  const [editing, setEditing] = React.useState<Set<number>>(new Set())
  const [stagedSpans, setStagedSpans] = React.useState<ClaimResponseSpan[]>([])
  const [activePosition, setActivePosition] = React.useState<number | null>(
    null,
  )
  const nextDraftId = React.useRef(
    Math.max(-1, ...groups.map((group) => group.id)) + 1,
  )
  const locked =
    !canSubmit || submitting || !!completionMessage || !!existingReview
  const owners = groups.map((group) => ({
    position: group.id,
    spans: group.finals.length
      ? group.finals.flatMap((claim) => claim.response_spans)
      : (group.original?.response_spans ?? []),
    dotClass: evalItemColor(group.id).dot,
    markClass: evalItemColor(group.id).mark,
  }))
  const finalClaims = groups.flatMap((group) => group.finals)

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
    const id = nextDraftId.current++
    setGroups((current) => [
      ...current,
      {
        id,
        finals: [
          {
            original_position: null,
            claim_text: claimText,
            label: "substantive",
            response_spans: stagedSpans,
          },
        ],
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
            Review verification relevance
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            Review every claim, including incidental claims. Substantive and
            borderline claims are included in verification. Labels describe
            relevance, not truth. Remove extraction errors; retain incidental
            and repeated claims.
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
                  Response or document
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  Click a color to focus its claim. Select exact text to add a
                  missing claim or replace a source in the editor.
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
                disabled={locked || stagedSpans.length === 0}
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
            groups={groups}
            locked={locked}
            stagedSpans={stagedSpans}
            onFocusClaim={focusSourceSpan}
            onChange={(id, finals) =>
              setGroups((current) =>
                current.map((group) =>
                  group.id === id ? { ...group, finals } : group,
                ),
              )
            }
            onEditing={(id, active) =>
              setEditing((current) => {
                const next = new Set(current)
                if (active) next.add(id)
                else next.delete(id)
                return next
              })
            }
          />
          <p className="text-sm text-muted-foreground">
            {finalClaims.length} final claims ·{" "}
            {
              groups.filter(
                (group) => group.original && group.finals.length > 1,
              ).length
            }{" "}
            split ·{" "}
            {
              groups.filter((group) => group.original && !group.finals.length)
                .length
            }{" "}
            removed
          </p>
          {editing.size > 0 && (
            <p className="text-sm">Apply or cancel edits before submitting.</p>
          )}
          {existingReview && (
            <p className="text-sm">
              Previously submitted correction. Read only.
            </p>
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
            disabled={locked || editing.size > 0 || finalClaims.length > 10000}
            onClick={() => onSubmit({ final_claims: finalClaims })}
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
