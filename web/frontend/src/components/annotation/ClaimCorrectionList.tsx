import { Trash2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"
import type { ClaimResponseOwner } from "./SelectableClaimResponse"

export const CLAIM_LABELS = [
  "vital",
  "supporting",
  "peripheral",
  "duplicate",
] as const
export type ClaimLabel = (typeof CLAIM_LABELS)[number]

export type CorrectionClaim = Omit<
  ClaimResponseOwner,
  "markClass" | "dotClass"
> & {
  claim: string
  proposedLabel: ClaimLabel
  colorClass: string
  dotClass: string
}

export type MissingClaimDraft = Omit<
  ClaimResponseOwner,
  "markClass" | "dotClass"
> & {
  draftId: string
  claim: string
  label: ClaimLabel
  colorClass: string
  dotClass: string
}

type ClaimCorrectionListProps = {
  claims: CorrectionClaim[]
  missingClaims: MissingClaimDraft[]
  labels: Record<number, ClaimLabel>
  onLabelChange: (position: number, label: ClaimLabel) => void
  onMissingChange: (
    draftId: string,
    patch: Partial<Pick<MissingClaimDraft, "claim" | "label">>,
  ) => void
  onRemoveMissing: (draftId: string) => void
  activePosition?: number | null
  onFocusClaim?: (position: number) => void
}

const labelDescriptions: Record<ClaimLabel, string> = {
  vital: "Directly answers a core part of the prompt",
  supporting: "Useful explanation or context",
  peripheral: "Endorsed but incidental information",
  duplicate: "Repeats an earlier claim",
}

function LabelButtons({
  label,
  onChange,
  name,
}: {
  label: ClaimLabel
  onChange: (label: ClaimLabel) => void
  name: string
}) {
  return (
    <fieldset className="grid grid-cols-2 gap-1 sm:flex sm:flex-wrap">
      <legend className="sr-only">{name} label</legend>
      {CLAIM_LABELS.map((option) => (
        <button
          aria-pressed={option === label}
          className={cn(
            "rounded-md border px-2.5 py-1.5 text-xs font-medium capitalize transition-colors",
            option === label
              ? "border-primary bg-primary text-primary-foreground shadow-sm"
              : "bg-background hover:bg-muted",
          )}
          key={option}
          onClick={() => onChange(option)}
          title={labelDescriptions[option]}
          type="button"
        >
          {option}
        </button>
      ))}
    </fieldset>
  )
}

function ClaimRow({
  claim,
  label,
  onLabelChange,
  active,
  onFocus,
}: {
  claim: CorrectionClaim
  label: ClaimLabel
  onLabelChange: (label: ClaimLabel) => void
  active: boolean
  onFocus: () => void
}) {
  return (
    <article
      className={cn(
        "scroll-mt-4 rounded-xl border border-l-4 bg-card p-4 shadow-sm transition-shadow",
        claim.colorClass,
        active && "ring-2 ring-primary/60",
      )}
      data-claim-position={claim.position}
      id={`claim-position-${claim.position}`}
    >
      <button
        className="block w-full text-left"
        onClick={onFocus}
        type="button"
      >
        <div className="mb-2 flex items-center justify-between gap-3">
          <span className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">
            <span
              aria-hidden="true"
              className={cn("size-2.5 rounded-full", claim.dotClass)}
            />
            Claim {claim.position + 1}
          </span>
          <span className="rounded-full border px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
            model proposal
          </span>
        </div>
        <p className="text-sm leading-6">{claim.claim}</p>
      </button>
      <div className="mt-4 rounded-lg border border-primary/30 bg-primary/[0.06] p-3">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <span className="text-2xl font-black capitalize tracking-tight text-primary">
            Proposed: {claim.proposedLabel}
          </span>
          <span className="text-xs text-muted-foreground">
            Change if needed
          </span>
        </div>
        <LabelButtons
          label={label}
          name={`Claim ${claim.position + 1}`}
          onChange={onLabelChange}
        />
      </div>
    </article>
  )
}

export function ClaimCorrectionList({
  activePosition = null,
  claims,
  labels,
  missingClaims,
  onFocusClaim,
  onLabelChange,
  onMissingChange,
  onRemoveMissing,
}: ClaimCorrectionListProps) {
  return (
    <section
      aria-label="Claims and labels"
      className="space-y-3"
      data-testid="claim-correction-list"
    >
      <div className="flex items-end justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">
            Decomposition
          </p>
          <h2 className="text-xl font-semibold tracking-tight">
            Review each claim
          </h2>
        </div>
        <span className="text-xs text-muted-foreground">
          {claims.length} model claim{claims.length === 1 ? "" : "s"}
        </span>
      </div>
      {claims.map((claim) => (
        <ClaimRow
          active={activePosition === claim.position}
          claim={claim}
          key={claim.position}
          label={labels[claim.position] ?? claim.proposedLabel}
          onFocus={() => onFocusClaim?.(claim.position)}
          onLabelChange={(label) => onLabelChange(claim.position, label)}
        />
      ))}
      {missingClaims.map((claim) => (
        <article
          className={cn(
            "rounded-xl border border-l-4 border-dashed bg-card p-4",
            claim.colorClass,
          )}
          data-testid={`missing-claim-${claim.draftId}`}
          id={`claim-position-${claim.position}`}
          key={claim.draftId}
        >
          <div className="mb-2 flex items-center justify-between gap-3">
            <span className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">
              <span
                aria-hidden="true"
                className={cn("size-2.5 rounded-full", claim.dotClass)}
              />
              Missing claim
            </span>
            <Button
              aria-label="Remove missing claim"
              onClick={() => onRemoveMissing(claim.draftId)}
              size="icon-sm"
              type="button"
              variant="ghost"
            >
              <Trash2 />
            </Button>
          </div>
          <Input
            aria-label="Missing claim text"
            onChange={(event) =>
              onMissingChange(claim.draftId, { claim: event.target.value })
            }
            value={claim.claim}
          />
          <div className="mt-3">
            <LabelButtons
              label={claim.label}
              name="Missing claim"
              onChange={(label) => onMissingChange(claim.draftId, { label })}
            />
          </div>
        </article>
      ))}
      {claims.length === 0 && missingClaims.length === 0 ? (
        <p className="rounded-xl border border-dashed p-4 text-sm text-muted-foreground">
          No claims proposed. Select response text to add one, or submit this
          empty decomposition.
        </p>
      ) : null}
    </section>
  )
}
